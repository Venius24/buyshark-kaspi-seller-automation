"""Add store-scoped api_keys and webhook_configs tables

Closes a real cross-tenant data leak: states/api_keys.json and
states/webhook_config.json had no store_id at all (Phase 4a's schema
migration explicitly skipped them — no DB-backed model existed for either,
see the removed NOTE comment in routers/api_keys.py). Any valid X-API-Key
could read/mutate ANY store's master-stock-groups through the public 1С/ERP
API (routers/public_api/groups.py), and the dashboard's own "revoke API key"
button could revoke a key belonging to a different store entirely.

api_keys becomes a real per-row table (id PK, store_id FK NOT NULL) instead
of a single shared JSON blob, since keys are independent entities with their
own lifecycle (create/revoke), unlike the "whole catalog as one blob"
tables from Слайс 1. webhook_configs is one row per store (store_id PK),
mirroring store_products — one webhook target/secret per tenant.

Backfill: production currently has empty states/api_keys.json and
states/webhook_config.json (no keys created, no webhook configured yet), so
there is nothing to migrate. The backfill code below is defensive — it only
runs if those files are non-empty AND exactly one Store exists, same rule as
migrations 002/006. Ambiguous cases (multiple stores) are skipped, not
guessed — the dashboard just shows no keys/config until recreated.

Revision ID: 007_api_keys_webhooks
Revises: 006_composite_store_keys
Create Date: 2026-07-31 00:00:00.000000

"""
import json
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '007_api_keys_webhooks'
down_revision: Union[str, None] = '006_composite_store_keys'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=50), primary_key=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.String(length=50), nullable=True),
        sa.Column("last_used", sa.String(length=50), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index("ix_api_keys_store_id", "api_keys", ["store_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_api_keys_store_id", "api_keys", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "webhook_configs",
        sa.Column("store_id", sa.String(length=36), primary_key=True),
        sa.Column("url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.String(length=50), nullable=True),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_webhook_configs_store_id", "webhook_configs", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    _backfill(bind)


def _backfill(bind) -> None:
    store_ids = bind.execute(sa.text("SELECT id FROM stores")).fetchall()
    if len(store_ids) != 1:
        return
    only_store_id = store_ids[0][0]

    if os.path.exists("states/api_keys.json"):
        try:
            with open("states/api_keys.json", "r", encoding="utf-8") as f:
                keys = json.load(f)
        except Exception:
            keys = {}
        for key_id, info in (keys or {}).items():
            bind.execute(
                sa.text(
                    "INSERT INTO api_keys (id, store_id, name, key_hash, prefix, created_at, last_used, active) "
                    "VALUES (:id, :sid, :name, :hash, :prefix, :created_at, :last_used, :active)"
                ),
                {
                    "id": key_id,
                    "sid": only_store_id,
                    "name": info.get("name", ""),
                    "hash": info.get("hash", ""),
                    "prefix": info.get("prefix", ""),
                    "created_at": info.get("created_at"),
                    "last_used": info.get("last_used"),
                    "active": bool(info.get("active", True)),
                },
            )

    if os.path.exists("states/webhook_config.json"):
        try:
            with open("states/webhook_config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        if cfg and cfg.get("url"):
            from backend.security.crypto import encrypt_data
            raw_secret = cfg.get("secret") or ""
            bind.execute(
                sa.text(
                    "INSERT INTO webhook_configs (store_id, url, encrypted_secret, enabled, updated_at) "
                    "VALUES (:sid, :url, :secret, :enabled, :updated_at)"
                ),
                {
                    "sid": only_store_id,
                    "url": cfg.get("url", ""),
                    "secret": encrypt_data(raw_secret) if raw_secret else None,
                    "enabled": bool(cfg.get("enabled", True)),
                    "updated_at": cfg.get("updated_at"),
                },
            )


def downgrade() -> None:
    op.drop_table("webhook_configs")
    op.drop_table("api_keys")
