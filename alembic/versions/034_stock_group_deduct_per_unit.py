"""Add master_stock_groups.deduct_per_unit — сколько штук общего остатка
съедает одна проданная единица товара.

До 14.08 это число выводилось из хвоста артикула ("T219-0005-4" -> 4), что в
живом каталоге оказалось номером варианта, а не фасовкой: заказ на 1 шт.
списывал 7. Угадывание убрано, размер комплекта теперь задаётся явно и по
умолчанию равен 1.

Revision ID: 034_stock_group_deduct_per_unit
Revises: 033_stock_group_oversold
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '034_stock_group_deduct_per_unit'
down_revision: Union[str, None] = '033_stock_group_oversold'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "master_stock_groups",
        sa.Column("deduct_per_unit", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("master_stock_groups", "deduct_per_unit")
