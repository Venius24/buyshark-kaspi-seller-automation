"""Add price_war_snapshots.url — ссылка на карточку товара в Kaspi для
сводки демпинг-войны на дашборде.

Revision ID: 032_price_war_url
Revises: 031_kaspi_offer_sync
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '032_price_war_url'
down_revision: Union[str, None] = '031_kaspi_offer_sync'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "price_war_snapshots",
        sa.Column("url", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("price_war_snapshots", "url")
