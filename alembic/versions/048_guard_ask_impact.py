"""Вопрос менеджеру о продавце без рейтинга: цена без него и ссылка на товар.

Revision ID: 048_guard_ask_impact
Revises: 047_dumping_guard
Create Date: 2026-10-05

С 05.10.2026 вопрос задаётся, только если решение меняет нашу цену, —
price_without хранит, какой она была бы без этого продавца. Заодно удаляются
вопросы о заглушке радара COMPETITOR_TOP_1 (это не продавец).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '048_guard_ask_impact'
down_revision: Union[str, None] = '047_dumping_guard'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('price_war_rival_decisions', sa.Column('price_without', sa.Float(), nullable=True))
    op.add_column('price_war_rival_decisions', sa.Column('url', sa.String(length=500), nullable=True))
    op.execute("DELETE FROM price_war_rival_decisions WHERE merchant_id LIKE 'COMPETITOR\\_%' ESCAPE '\\'")


def downgrade() -> None:
    op.drop_column('price_war_rival_decisions', 'url')
    op.drop_column('price_war_rival_decisions', 'price_without')
