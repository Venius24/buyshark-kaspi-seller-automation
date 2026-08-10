"""Add sales_orders.review_eligible — кампания «Отзыв», скан по дням.

Раньше кампания «Отзыв» ставилась в очередь один раз, сразу при обнаружении
завершённого заказа (backend/chatbot/review_campaign.py), с холодным 90-дневным
бэкафиллом нового магазина исключённым на уровне вызывающего кода
(sales_sync_worker.py не вызывал триггер на is_cold_start). Переход на
периодический скан sales_orders по дням с момента ingested_at требует того же
исключения, но теперь на уровне самой таблицы — колонка проставляется False
при вставке заказов холодного бэкафилла, True при обычной вставке (см.
_upsert_sales_orders). Скан кампании «Отзыв» фильтрует WHERE review_eligible=True.

Revision ID: 025_review_eligible
Revises: 024_master_stock_group_stop_qty
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '025_review_eligible'
down_revision: Union[str, None] = '024_master_stock_group_stop_qty'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sales_orders",
        sa.Column("review_eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("sales_orders", "review_eligible")
