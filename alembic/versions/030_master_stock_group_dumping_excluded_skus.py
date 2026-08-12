"""Add master_stock_groups.dumping_excluded_skus.

Позволяет исключить конкретные SKU из группового авто-демпинга
(dumping_config на уровне Мастер-Группы), не трогая при этом
разделение остатка (shared_stock) — исключённый товар просто не
получает групповой dumping_config в backend/dumper/__init__.py,
но его собственный individual dumping_config (dumping_configs) как
работал, так и работает.

Revision ID: 030_master_stock_group_dumping_excluded_skus
Revises: 029_client_mailboxes
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '030_master_stock_group_dumping_excluded_skus'
down_revision: Union[str, None] = '029_client_mailboxes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "master_stock_groups",
        sa.Column("dumping_excluded_skus", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("master_stock_groups", "dumping_excluded_skus")
