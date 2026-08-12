"""Add kaspi_offer_sync — единое состояние синхронизации оффера с Kaspi.

Одна таблица вместо связки "очередь + журнал + счётчик": желаемое состояние,
последняя отправка и фактическое состояние на Kaspi лежат в одной строке на
(store_id, sku). Очередь на запись — это выборка строк, где desired_*
расходится с фактом, отсортированная по priority/desired_at.

Счётчика квоты здесь нет намеренно: Kaspi отдаёт своё состояние окна через
/pricefeed/upload/merchant/upload/limit/settings, и оно и есть источник правды
(см. backend/services/kaspi_quota.py).

Revision ID: 031_kaspi_offer_sync
Revises: 030_stock_group_dump_exclusions
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '031_kaspi_offer_sync'
down_revision: Union[str, None] = '030_stock_group_dump_exclusions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kaspi_offer_sync",
        sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sku", sa.String(255), primary_key=True),
        sa.Column("master_sku", sa.String(100), nullable=True),
        sa.Column("model", sa.String(500), nullable=True),
        sa.Column("store_suffix", sa.String(20), nullable=True, server_default="001"),
        sa.Column("city_id", sa.String(20), nullable=True, server_default="750000000"),
        sa.Column("desired_price", sa.Integer(), nullable=True),
        sa.Column("desired_stock", sa.Integer(), nullable=True),
        sa.Column("desired_available", sa.Boolean(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True, server_default="3"),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("desired_at", sa.DateTime(), nullable=True),
        sa.Column("pushed_price", sa.Integer(), nullable=True),
        sa.Column("pushed_stock", sa.Integer(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(), nullable=True),
        sa.Column("kaspi_price", sa.Integer(), nullable=True),
        sa.Column("kaspi_stock", sa.Integer(), nullable=True),
        sa.Column("kaspi_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    # Горячий индекс флашера: "что писать дальше" — строки с намерением,
    # отсортированные по приоритету и возрасту.
    op.create_index(
        "ix_kaspi_offer_sync_queue",
        "kaspi_offer_sync",
        ["store_id", "priority", "desired_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_kaspi_offer_sync_queue", table_name="kaspi_offer_sync")
    op.drop_table("kaspi_offer_sync")
