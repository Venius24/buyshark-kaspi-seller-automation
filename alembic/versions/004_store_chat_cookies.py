"""Add nullable encrypted_chat_cookies to stores

Additive-only migration. Kaspi chat/messaging API requires a session
established via phone+SMS login (idmc.shop.kaspi.kz), distinct from the
email+password session used for pricing/dumping/orders (encrypted_cookies).
Storing it in a separate column keeps the two sessions from overwriting
each other.

Revision ID: 004_store_chat_cookies
Revises: 003_store_mc_credentials
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004_store_chat_cookies'
down_revision: Union[str, None] = '003_store_mc_credentials'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("encrypted_chat_cookies", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("stores", "encrypted_chat_cookies")
