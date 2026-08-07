"""Add telegram_links, telegram_link_tokens tables + users.is_telegram_only —
identification layer for the "store in your pocket" Telegram bot: deep-link
привязка кабинета к Telegram (t.me/<bot>?start=<token>) вместо ручного ввода
числового Telegram ID, плюс приглашение сотрудников без веб-аккаунта.

Revision ID: 015_telegram_bot
Revises: 014_price_war_chat_logs
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '015_telegram_bot'
down_revision: Union[str, None] = '014_price_war_chat_logs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.add_column("users", sa.Column("is_telegram_only", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "telegram_links",
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("active_store_id", sa.String(length=36), nullable=True),
        sa.Column("tg_username", sa.String(length=255), nullable=True),
        sa.Column("tg_first_name", sa.String(length=255), nullable=True),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("tg_user_id"),
    )
    op.create_index("ix_telegram_links_user_id", "telegram_links", ["user_id"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_telegram_links_user_id", "telegram_links", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        op.create_foreign_key(
            "fk_telegram_links_active_store_id", "telegram_links", "stores", ["active_store_id"], ["id"], ondelete="SET NULL"
        )

    op.create_table(
        "telegram_link_tokens",
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("invite_role", sa.String(length=50), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_telegram_link_tokens_store_id", "telegram_link_tokens", ["store_id"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_telegram_link_tokens_user_id", "telegram_link_tokens", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        op.create_foreign_key(
            "fk_telegram_link_tokens_store_id", "telegram_link_tokens", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )
        op.create_foreign_key(
            "fk_telegram_link_tokens_created_by", "telegram_link_tokens", "users", ["created_by"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("telegram_link_tokens")
    op.drop_table("telegram_links")
    op.drop_column("users", "is_telegram_only")
