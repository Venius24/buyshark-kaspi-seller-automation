"""Add auto_assembly_rules, auto_assembly_log, waybill_spool tables — the
«Автосборка» feature: per-delivery-type rules for auto-accept / auto-assemble
/ auto-download-waybill, plus a decision log and a spool of downloaded
waybill PDFs feeding the print batcher.

Revision ID: 012_autoassembly
Revises: 011_sales_history
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '012_autoassembly'
down_revision: Union[str, None] = '011_sales_history'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "auto_assembly_rules",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("delivery_type", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stage_accept", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stage_assemble", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stage_waybill", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("schedule", sa.Text(), nullable=True),
        sa.Column("max_order_price", sa.Float(), nullable=True),
        sa.Column("sku_blacklist", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("respect_stock_groups", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_per_cycle", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id", "delivery_type"),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_auto_assembly_rules_store_id", "auto_assembly_rules", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "auto_assembly_log",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("order_code", sa.String(length=100), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auto_assembly_log_store_id", "auto_assembly_log", ["store_id"])
    op.create_index("ix_auto_assembly_log_created_at", "auto_assembly_log", ["created_at"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_auto_assembly_log_store_id", "auto_assembly_log", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "waybill_spool",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("order_code", sa.String(length=100), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(), nullable=False),
        sa.Column("printed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("store_id", "order_code"),
    )
    op.create_index("ix_waybill_spool_date", "waybill_spool", ["date"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_waybill_spool_store_id", "waybill_spool", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("waybill_spool")
    op.drop_table("auto_assembly_log")
    op.drop_table("auto_assembly_rules")
