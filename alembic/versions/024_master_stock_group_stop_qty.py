"""Add master_stock_groups.stop_qty — кастомное "Стоп кол-во" на группу.

Раньше группа снималась с продажи (архивировалась на Kaspi) только при
shared_stock == 0, хардкод. Теперь порог настраивается per-группа
(по умолчанию 5) — при просаде остатка ниже/равно этому значению группа
уходит в архив на Kaspi и шлётся телеграм-уведомление (см.
backend/services/stock_sync_worker.py::execute_broadcast_sync).

Revision ID: 024_master_stock_group_stop_qty
Revises: 023_outbound_stats
Create Date: 2026-08-10 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '024_master_stock_group_stop_qty'
down_revision: Union[str, None] = '023_outbound_stats'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "master_stock_groups",
        sa.Column("stop_qty", sa.Integer(), nullable=False, server_default="5"),
    )


def downgrade() -> None:
    op.drop_column("master_stock_groups", "stop_qty")
