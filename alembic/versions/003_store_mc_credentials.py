"""Add nullable encrypted_mc_email/encrypted_mc_password to stores

Additive-only migration (Phase 3.1 closure). Lets per-store MC session
auto-refresh look up that store's own Kaspi MC email/password instead of
the single global states/session.json credential pair.

Revision ID: 003_store_mc_credentials
Revises: 002_store_scoped_tables
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '003_store_mc_credentials'
down_revision: Union[str, None] = '002_store_scoped_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("encrypted_mc_email", sa.Text(), nullable=True))
    op.add_column("stores", sa.Column("encrypted_mc_password", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("stores", "encrypted_mc_password")
    op.drop_column("stores", "encrypted_mc_email")
