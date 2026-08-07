"""Add store_print_settings — per-store Print-Batcher settings.

Санитарный фикс, найденный при планировании Telegram-бота (см.
docs/TELEGRAM_BOT_PLAN.md §7.2): print_settings.json был ОДНИМ файлом на все
магазины — при появлении второго тенанта настройки печати (раскладка по
умолчанию, экспресс-режим, дедупликация) молча делились бы между магазинами.

Revision ID: 016_store_print_settings
Revises: 015_telegram_bot
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '016_store_print_settings'
down_revision: Union[str, None] = '015_telegram_bot'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "store_print_settings",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id"),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_store_print_settings_store_id", "store_print_settings", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("store_print_settings")
