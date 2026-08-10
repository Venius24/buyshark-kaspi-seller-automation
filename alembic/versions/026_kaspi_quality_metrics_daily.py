"""Add kaspi_quality_metrics_daily table — one row per (store_id, date),
upserted once per night by backend/services/kaspi_quality_metrics_worker.py
from the Kaspi MC "Показатели качества" GraphQL API (getSingleMetricDetails /
getAverageChatResponseTime / getChatResponseRate — see
REAL_IMPORTANT/показатели_качества/KASPI_QUALITY_METRICS_API.md). Powers the
new "Показатели Kaspi" tab in the ScoreRing dashboard card.

Revision ID: 026_kaspi_quality_metrics_daily
Revises: 025_review_eligible
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '026_kaspi_quality_metrics_daily'
down_revision: Union[str, None] = '025_review_eligible'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "kaspi_quality_metrics_daily",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("rating_percentage", sa.Float(), nullable=True),
        sa.Column("returns_percentage", sa.Float(), nullable=True),
        sa.Column("late_kd_percentage", sa.Float(), nullable=True),
        sa.Column("cancelled_percentage", sa.Float(), nullable=True),
        sa.Column("avg_chat_response_minutes", sa.Float(), nullable=True),
        sa.Column("chat_response_rate_percent", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id", "date"),
    )
    op.create_index("ix_kaspi_quality_metrics_daily_store_date", "kaspi_quality_metrics_daily", ["store_id", "date"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_kaspi_quality_metrics_daily_store_id", "kaspi_quality_metrics_daily", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("kaspi_quality_metrics_daily")
