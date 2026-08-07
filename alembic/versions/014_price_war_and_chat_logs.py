"""Add price_war_snapshots, competitor_encounter_logs, chat_response_logs.

Powers: DumpingTab price-war summary (all SKUs, not just one), competitor
leaderboard widget, chat activity heatmap. price_war_snapshots is piggybacked
onto the existing ~5-minute dumping cycle (backend/dumper/brain.py) — the
cycle already fetches competitor offers per enabled SKU to decide reactions,
so this adds zero extra Kaspi API calls. All three tables start empty and
accumulate only from the moment this migration deploys (no backfill).

Revision ID: 014_price_war_chat_logs
Revises: 013_analytics_daily_history
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '014_price_war_chat_logs'
down_revision: Union[str, None] = '013_analytics_daily_history'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "price_war_snapshots",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("our_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("our_rank", sa.Integer(), nullable=True),
        sa.Column("competitors_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_competitor_price", sa.Float(), nullable=True),
        sa.Column("best_competitor_name", sa.String(length=300), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id", "sku"),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_price_war_snapshots_store_id", "price_war_snapshots", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "competitor_encounter_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("competitor_name", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_competitor_encounter_logs_store_id", "competitor_encounter_logs", ["store_id"])
    op.create_index("ix_competitor_encounter_logs_created_at", "competitor_encounter_logs", ["created_at"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_competitor_encounter_logs_store_id", "competitor_encounter_logs", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )

    op.create_table(
        "chat_response_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chat_response_logs_store_id", "chat_response_logs", ["store_id"])
    op.create_index("ix_chat_response_logs_created_at", "chat_response_logs", ["created_at"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_chat_response_logs_store_id", "chat_response_logs", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("chat_response_logs")
    op.drop_table("competitor_encounter_logs")
    op.drop_table("price_war_snapshots")
