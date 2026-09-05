"""Метки на товарах для печати рядом со штампом группы (2026-09-06).

Revision ID: 042_product_tags
Revises: 041_assistant_skeleton
Create Date: 2026-09-06 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '042_product_tags'
down_revision: Union[str, None] = '041_assistant_skeleton'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_tags",
        sa.Column("store_id", sa.String(length=36), sa.ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sku", sa.String(length=100), primary_key=True),
        sa.Column("tag", sa.String(length=60), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    # Печать спрашивает теги пачкой по артикулам одной партии, кабинет — по
    # магазину целиком; ведущий store_id составного PK второй случай уже
    # закрывает, отдельный индекс нужен только под первый.
    op.create_index("ix_product_tags_store_sku", "product_tags", ["store_id", "sku"])


def downgrade() -> None:
    op.drop_index("ix_product_tags_store_sku", table_name="product_tags")
    op.drop_table("product_tags")
