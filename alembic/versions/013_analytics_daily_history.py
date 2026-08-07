"""Add analytics_daily_history table — one row per (store_id, date), upserted
once per day from the real dumping-cycle snapshot (see backend/dumper/__init__.py).
Powers the new score-ring trend / streak counters on the Overview dashboard;
AnalyticsSnapshotDB only ever holds the latest snapshot, so it can't answer
"how many days in a row" questions.

Revision ID: 013_analytics_daily_history
Revises: 012_autoassembly
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '013_analytics_daily_history'
down_revision: Union[str, None] = '012_autoassembly'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "analytics_daily_history",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("best_price_share", sa.Float(), nullable=False, server_default="0"),
        sa.Column("winning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_saved_margin", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id", "date"),
    )
    op.create_index("ix_analytics_daily_history_store_date", "analytics_daily_history", ["store_id", "date"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_analytics_daily_history_store_id", "analytics_daily_history", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("analytics_daily_history")
