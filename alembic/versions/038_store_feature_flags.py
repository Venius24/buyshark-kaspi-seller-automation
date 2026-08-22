"""Add store_feature_flags — общемагазинные выключатели демпинга/связки
остатков/автосборки для вкладки "Глобальное управление" (2026-08-22).

Revision ID: 038_store_feature_flags
Revises: 037_user_onboarding_completed_at
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '038_store_feature_flags'
down_revision: Union[str, None] = '037_user_onboarding_completed_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_feature_flags",
        sa.Column("store_id", sa.String(length=36), sa.ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("dumping_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stock_groups_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("autoassembly_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("store_feature_flags")
