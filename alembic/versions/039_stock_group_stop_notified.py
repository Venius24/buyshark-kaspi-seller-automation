"""Add master_stock_groups.stop_notified — персистит анти-спам флаг "уже
уведомили о снятии этой группы по стоп-кол-ву". Раньше notify_zero_stock_telegram
стреляла на КАЖДОМ вызове _execute_broadcast_sync_impl, пока shared_stock <=
stop_qty (broadcast_sync.py:436) — то есть на каждый следующий заказ по уже
остановленной группе, а не только на переход через порог. Только 10-минутный
in-memory дедуп в notify_dispatch отделял повторы, поэтому владелец получал
одно и то же "снято с продажи" многократно за день, пока группа не пополнится
(жалоба 2026-09-03: "уведомления даже по поводу снятых из мастер-группы").

Флаг сбрасывается в False при пополнении (shared_stock > stop_qty) — так что
следующее исчерпание снова уведомит один раз, честно.

Revision ID: 039_stock_group_stop_notified
Revises: 038_store_feature_flags
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '039_stock_group_stop_notified'
down_revision: Union[str, None] = '038_store_feature_flags'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "master_stock_groups",
        sa.Column("stop_notified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("master_stock_groups", "stop_notified")
