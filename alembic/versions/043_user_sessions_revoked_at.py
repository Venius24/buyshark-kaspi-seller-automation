"""users.sessions_revoked_at — отзыв сессий при сбросе пароля (2026-09-25).

Revision ID: 043_user_sessions_revoked_at
Revises: 042_product_tags
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '043_user_sessions_revoked_at'
down_revision: Union[str, None] = '042_product_tags'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable без default: у всех существующих пользователей NULL —
    # уже выданные токены продолжают работать.
    op.add_column("users", sa.Column("sessions_revoked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "sessions_revoked_at")
