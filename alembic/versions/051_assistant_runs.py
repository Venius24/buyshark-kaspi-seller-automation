"""Assistant run observability. Migration is not applied by the eval runner."""
import sqlalchemy as sa
from alembic import op

revision = "051_assistant_runs"
down_revision = "050_assistant_price_proposals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistant_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("assistant_conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("tool_errors", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("steps", sa.Text(), nullable=False),
    )
    op.create_index("ix_assistant_runs_store_started", "assistant_runs", ["store_id", "started_at"])


def downgrade():
    op.drop_index("ix_assistant_runs_store_started", table_name="assistant_runs")
    op.drop_table("assistant_runs")
