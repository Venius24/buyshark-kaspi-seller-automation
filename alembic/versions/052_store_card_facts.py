"""Persistent public store card facts. Apply separately from import."""
import sqlalchemy as sa
from alembic import op

revision = "052_store_card_facts"
down_revision = "051_assistant_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "store_card_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("name", sa.String(300), nullable=True),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("features", sa.Text(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("store_id", "sku", name="uq_store_card_facts_store_sku"),
    )
    op.create_index("ix_store_card_facts_store_id", "store_card_facts", ["store_id"])


def downgrade():
    op.drop_index("ix_store_card_facts_store_id", table_name="store_card_facts")
    op.drop_table("store_card_facts")
