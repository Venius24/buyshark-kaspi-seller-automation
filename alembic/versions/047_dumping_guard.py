"""Защита демпинга от оттеснения (backend/dumper/guard.py):
- store_dumping_guard — общие настройки магазина;
- dumping_configs.guard_config — переопределение на товаре;
- price_war_moves — история наших ходов и соперника (лесенка, отчёт);
- price_war_snapshots.guard_json — сигналы атаки последнего цикла;
- price_war_rival_decisions — решения менеджера по продавцам без рейтинга.

Revision ID: 047_dumping_guard
Revises: 046_chat_login_phone
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '047_dumping_guard'
down_revision: Union[str, None] = '046_chat_login_phone'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_dumping_guard",
        sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "price_war_moves",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("our_old", sa.Float(), nullable=False),
        sa.Column("our_new", sa.Float(), nullable=False),
        sa.Column("rival_id", sa.String(64), nullable=True),
        sa.Column("rival_name", sa.String(300), nullable=True),
        sa.Column("rival_price", sa.Float(), nullable=True),
        sa.Column("rival_reviews", sa.Integer(), nullable=True),
        sa.Column("rival_rating", sa.Float(), nullable=True),
        sa.Column("rival_intercity", sa.Boolean(), nullable=True),
        sa.Column("action", sa.String(50), nullable=True),
    )
    op.create_index("ix_price_war_moves_store_sku_created", "price_war_moves", ["store_id", "sku", "created_at"])
    op.add_column("dumping_configs", sa.Column("guard_config", sa.Text(), nullable=True))
    op.add_column("price_war_snapshots", sa.Column("guard_json", sa.Text(), nullable=True))
    op.create_table(
        "price_war_rival_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("merchant_id", sa.String(64), nullable=False),
        sa.Column("merchant_name", sa.String(300), nullable=True),
        sa.Column("product_name", sa.String(500), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ASK"),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("our_price", sa.Float(), nullable=True),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(200), nullable=True),
        sa.UniqueConstraint("store_id", "sku", "merchant_id", name="uq_price_war_rival_decision"),
    )
    op.create_index("ix_price_war_rival_decisions_store_id", "price_war_rival_decisions", ["store_id"])


def downgrade() -> None:
    op.drop_index("ix_price_war_rival_decisions_store_id", table_name="price_war_rival_decisions")
    op.drop_table("price_war_rival_decisions")
    op.drop_column("price_war_snapshots", "guard_json")
    op.drop_column("dumping_configs", "guard_config")
    op.drop_index("ix_price_war_moves_store_sku_created", table_name="price_war_moves")
    op.drop_table("price_war_moves")
    op.drop_table("store_dumping_guard")
