"""Add sales_orders table — real revenue/sales history, backfilled by
backend/services/sales_sync_worker.py from Kaspi Order API (state=ARCHIVE,
status=COMPLETED). Replaces the fabricated numbers previously shown on the
Overview dashboard (see HANDOFF.md, "реальная статистика" front).

Revision ID: 011_sales_history
Revises: 010_draft_products
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '011_sales_history'
down_revision: Union[str, None] = '010_draft_products'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "sales_orders",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("order_internal_id", sa.String(length=100), nullable=False),
        sa.Column("order_code", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("total_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("entries_payload", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id", "order_internal_id"),
    )
    op.create_index("ix_sales_orders_created_at", "sales_orders", ["created_at"])
    op.create_index("ix_sales_orders_store_created", "sales_orders", ["store_id", "created_at"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_sales_orders_store_id", "sales_orders", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("sales_orders")
