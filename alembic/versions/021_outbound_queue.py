"""Add outbound_queue + order_chat_links — каркас исходящих рассылок (Фаза 3).

Общая инфраструктура для будущих кампаний «Самовывоз» (Фаза 4) и «Отзыв»
(Фаза 5): очередь сообщений с дедупом на уровне БД (unique store_id+order_id+
campaign — одна кампания на заказ), гейтами (тихие часы, human_override,
дневной лимит — применяются в backend/chatbot/outbound.py, не в схеме) и
кэшем order_id -> group_id, чтобы Playwright-автоматизация первого контакта
запускалась только один раз на заказ, а не на каждое сообщение.

Исходящие НЕ пишутся в chat_response_logs (Фаза 2 тарификация) — по решению
пользователя рассылки не считаются как ответы ИИ.

Revision ID: 021_outbound_queue
Revises: 020_chat_response_log_billing
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '021_outbound_queue'
down_revision: Union[str, None] = '020_chat_response_log_billing'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "outbound_queue",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=100), nullable=False),
        sa.Column("campaign", sa.String(length=30), nullable=False),
        sa.Column("group_id", sa.String(length=100), nullable=True),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("window_start_hour", sa.Integer(), nullable=True),
        sa.Column("window_end_hour", sa.Integer(), nullable=True),
        sa.Column("skip_reason", sa.String(length=200), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("store_id", "order_id", "campaign", name="uq_outbound_queue_store_order_campaign"),
    )
    op.create_index("ix_outbound_queue_store_id", "outbound_queue", ["store_id"])
    op.create_index("ix_outbound_queue_status_scheduled", "outbound_queue", ["status", "scheduled_at"])

    op.create_table(
        "order_chat_links",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=100), nullable=False),
        sa.Column("group_id", sa.String(length=100), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id", "order_id"),
    )

    if not is_sqlite:
        op.create_foreign_key(
            "fk_outbound_queue_store_id", "outbound_queue", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )
        op.create_foreign_key(
            "fk_order_chat_links_store_id", "order_chat_links", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("order_chat_links")
    op.drop_index("ix_outbound_queue_status_scheduled", table_name="outbound_queue")
    op.drop_index("ix_outbound_queue_store_id", table_name="outbound_queue")
    op.drop_table("outbound_queue")
