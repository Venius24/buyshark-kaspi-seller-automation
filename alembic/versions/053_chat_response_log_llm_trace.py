"""Трасса вызова LLM в журнале ответов автоответчика (backend/chatbot/llm.py)."""
import sqlalchemy as sa
from alembic import op

revision = "053_chat_response_log_llm_trace"
down_revision = "052_store_card_facts"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("chat_response_logs") as batch:
        batch.add_column(sa.Column("latency_ms", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("llm_attempts", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cost_usd", sa.Float(), nullable=True))
        batch.add_column(sa.Column("prompt_version", sa.String(20), nullable=True))
        batch.add_column(sa.Column("llm_parse", sa.String(10), nullable=True))
        batch.add_column(sa.Column("llm_action", sa.String(16), nullable=True))


def downgrade():
    with op.batch_alter_table("chat_response_logs") as batch:
        batch.drop_column("llm_action")
        batch.drop_column("llm_parse")
        batch.drop_column("prompt_version")
        batch.drop_column("cost_usd")
        batch.drop_column("llm_attempts")
        batch.drop_column("latency_ms")
