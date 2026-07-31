"""Add store-scoped chatbot_settings and chat_states tables

Closes another cross-tenant gap: the chat-bot's enabled/admin_ids config
(states/chatbot_config.json + states/admin_ids.txt) and the in-memory
PROCESSED_CHATS dict were global — one store's owner disabling the bot
turned it off for every store, and Telegram escalation alerts for any
store's chat went to the same single admin list. chatbot_settings gives
each store its own enabled flag + admin_ids; chat_states persists the
HUMAN_OVERRIDE / last-replied-message state per (store_id, group_id) so it
survives a restart, same as it did before (PROCESSED_CHATS just happened to
live only in memory).

The Telegram bot process itself stays a single shared instance (out of
scope — see HANDOFF) — only the per-store DATA it reads/writes is scoped
here.

Backfill: mirrors 007's rule — only when exactly one Store exists and the
legacy files/dict have content. PROCESSED_CHATS was never persisted to
disk, so there is nothing to backfill for chat_states (starts empty,
exactly like a fresh in-memory dict would after any past restart).

Revision ID: 008_chatbot_per_store
Revises: 007_api_keys_webhooks
Create Date: 2026-08-01 00:00:00.000000

"""
import json
import os
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '008_chatbot_per_store'
down_revision: Union[str, None] = '007_api_keys_webhooks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "chatbot_settings",
        sa.Column("store_id", sa.String(length=36), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("admin_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.String(length=50), nullable=True),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_chatbot_settings_store_id", "chatbot_settings", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "chat_states",
        sa.Column("store_id", sa.String(length=36), primary_key=True),
        sa.Column("group_id", sa.String(length=100), primary_key=True),
        sa.Column("last_replied_msg_id", sa.String(length=100), nullable=True),
        sa.Column("human_override", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("updated_at", sa.String(length=50), nullable=True),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_chat_states_store_id", "chat_states", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    _backfill(bind)


def _backfill(bind) -> None:
    store_ids = bind.execute(sa.text("SELECT id FROM stores")).fetchall()
    if len(store_ids) != 1:
        return
    only_store_id = store_ids[0][0]

    enabled = True
    admin_ids = None
    if os.path.exists("states/chatbot_config.json"):
        try:
            with open("states/chatbot_config.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if "enabled" in cfg:
                enabled = bool(cfg["enabled"])
            if cfg.get("admin_ids"):
                admin_ids = cfg["admin_ids"]
        except Exception:
            pass

    if admin_ids is None and os.path.exists("states/admin_ids.txt"):
        try:
            with open("states/admin_ids.txt", "r", encoding="utf-8") as f:
                ids = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
            if ids:
                admin_ids = ids
        except Exception:
            pass

    if admin_ids is None:
        admin_ids = ["7119137844", "8661441552"]

    bind.execute(
        sa.text("INSERT INTO chatbot_settings (store_id, enabled, admin_ids) VALUES (:sid, :enabled, :admin_ids)"),
        {"sid": only_store_id, "enabled": enabled, "admin_ids": json.dumps(admin_ids, ensure_ascii=False)},
    )


def downgrade() -> None:
    op.drop_table("chat_states")
    op.drop_table("chatbot_settings")
