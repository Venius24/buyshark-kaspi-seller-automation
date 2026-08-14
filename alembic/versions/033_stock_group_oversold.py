"""Add master_stock_groups.oversold — долг группы, когда заказов пришло
больше, чем числилось остатка.

Остаток обрезается по нулю (отрицательное число нельзя слать в Kaspi), из-за
чего нехватка терялась молча: журнал показывал "было 4 → стало 0" и ни слова
о том, что трёх штук нет. Долг копится здесь и обнуляется при ручном вводе
остатка.

Revision ID: 033_stock_group_oversold
Revises: 032_price_war_url
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '033_stock_group_oversold'
down_revision: Union[str, None] = '032_price_war_url'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "master_stock_groups",
        sa.Column("oversold", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("master_stock_groups", "oversold")
