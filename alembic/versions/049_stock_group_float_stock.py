"""Дробный остаток Мастер-Групп: shared_stock и oversold -> Float.

Revision ID: 049_stock_group_float_stock
Revises: 048_guard_ask_impact
Create Date: 2026-10-06

Рецепт списания артикула (sku_recipes.qty) стал дробным — 0.5 м за продажу,
четверть упаковки и т.п. Чтобы списать дробь, остаток группы и долг
перепродажи тоже должны быть дробными. На Kaspi по-прежнему уходит целое
(floor), см. backend/services/sku_recipes.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '049_stock_group_float_stock'
down_revision: Union[str, None] = '048_guard_ask_impact'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('master_stock_groups') as b:
        b.alter_column('shared_stock', type_=sa.Float(), existing_type=sa.Integer(),
                       existing_nullable=True)
        b.alter_column('oversold', type_=sa.Float(), existing_type=sa.Integer(),
                       existing_nullable=False, existing_server_default='0')


def downgrade() -> None:
    with op.batch_alter_table('master_stock_groups') as b:
        b.alter_column('shared_stock', type_=sa.Integer(), existing_type=sa.Float(),
                       existing_nullable=True, postgresql_using='floor(shared_stock)::integer')
        b.alter_column('oversold', type_=sa.Integer(), existing_type=sa.Float(),
                       existing_nullable=False, existing_server_default='0',
                       postgresql_using='ceil(oversold)::integer')
