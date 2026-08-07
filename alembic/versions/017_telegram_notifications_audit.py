"""Add telegram_notification_prefs and telegram_audit_log — Ф4/Ф5 of
docs/TELEGRAM_BOT_PLAN.md (§4.1, §4.4, §6).

Revision ID: 017_telegram_notif_audit
Revises: 016_store_print_settings
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '017_telegram_notif_audit'
down_revision: Union[str, None] = '016_store_print_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "telegram_notification_prefs",
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("chats", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("problems", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("money", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("new_order_min_sum", sa.Integer(), nullable=True),
        sa.Column("quiet_from", sa.String(length=5), nullable=True),
        sa.Column("quiet_to", sa.String(length=5), nullable=True),
        sa.PrimaryKeyConstraint("tg_user_id", "store_id"),
    )

    op.create_table(
        "telegram_audit_log",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_telegram_audit_log_store_id", "telegram_audit_log", ["store_id"])
    op.create_index("ix_telegram_audit_log_created_at", "telegram_audit_log", ["created_at"])

    if not is_sqlite:
        op.create_foreign_key(
            "fk_telegram_notification_prefs_tg_user_id", "telegram_notification_prefs",
            "telegram_links", ["tg_user_id"], ["tg_user_id"], ondelete="CASCADE",
        )
        op.create_foreign_key(
            "fk_telegram_notification_prefs_store_id", "telegram_notification_prefs",
            "stores", ["store_id"], ["id"], ondelete="CASCADE",
        )
        op.create_foreign_key(
            "fk_telegram_audit_log_store_id", "telegram_audit_log",
            "stores", ["store_id"], ["id"], ondelete="CASCADE",
        )


def downgrade() -> None:
    op.drop_index("ix_telegram_audit_log_created_at", table_name="telegram_audit_log")
    op.drop_index("ix_telegram_audit_log_store_id", table_name="telegram_audit_log")
    op.drop_table("telegram_audit_log")
    op.drop_table("telegram_notification_prefs")
