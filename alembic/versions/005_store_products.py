"""Add store_products table (per-store product catalog cache)

Closes the multi-tenancy gap where states/products.json was a single file
shared by every store — any user opening the Products tab saw whichever
store's catalog was synced last (products_sync_worker.py only ever synced
the first-created store when more than one was active). This table gives
each store its own cached catalog row, keyed by store_id, mirroring the
existing store-scoped tables from migration 002.

Additive-only: no existing table/column touched. Backfill mirrors 002's
rule — only when exactly one Store row exists (today's production reality)
do we know unambiguously which store the existing states/products.json
cache belongs to; ambiguous cases are left for the app to repopulate on
next sync rather than guessed.

Revision ID: 005_store_products
Revises: 004_store_chat_cookies
Create Date: 2026-07-31 00:00:00.000000

"""
import json
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '005_store_products'
down_revision: Union[str, None] = '004_store_chat_cookies'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "store_products",
        sa.Column("store_id", sa.String(length=36), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_store_products_store_id", "store_products", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    # Backfill: only when exactly one Store exists, and only when the legacy
    # shared cache file is actually present, do we know unambiguously where
    # its contents belong.
    store_ids = bind.execute(sa.text("SELECT id FROM stores")).fetchall()
    if len(store_ids) == 1 and os.path.exists("states/products.json"):
        try:
            with open("states/products.json", "r", encoding="utf-8") as f:
                products = json.load(f)
            only_store_id = store_ids[0][0]
            bind.execute(
                sa.text("INSERT INTO store_products (store_id, payload) VALUES (:sid, :payload)"),
                {"sid": only_store_id, "payload": json.dumps(products, ensure_ascii=False)},
            )
        except Exception:
            pass  # legacy file missing/corrupt — next sync cycle repopulates it


def downgrade() -> None:
    op.drop_table("store_products")
