"""Add draft_products table for the «Автодобавление товара» (autolist) feature

New feature, not backfilling anything legacy — a draft is created going
forward whenever a user uploads a photo+title for AI-assisted product
creation (backend/autolist/). See HANDOFF.md section 3.17 and
ПСЕВДО_ДОКУМЕНТАЦИЯ_КАСПИ/07_ДОБАВЛЕНИЕ_ТОВАРА.md for the reverse-engineered
Kaspi flow this table's fields are shaped around.

Revision ID: 010_draft_products
Revises: 009_operational_state_per_store
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '010_draft_products'
down_revision: Union[str, None] = '009_operational_state_per_store'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "draft_products",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("photo_path", sa.String(length=500), nullable=False),
        sa.Column("raw_title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("ai_raw", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("draft_payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("missing_mandatory", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("kaspi_result", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_draft_products_store_id", "draft_products", ["store_id"])
    op.create_index("ix_draft_products_status", "draft_products", ["status"])
    if not is_sqlite:
        op.create_foreign_key(
            "fk_draft_products_store_id", "draft_products", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("draft_products")
