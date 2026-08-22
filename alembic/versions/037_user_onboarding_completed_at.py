"""Add users.onboarding_completed_at — персистит прохождение онбординг-тура
в кабинете, per-user (не per-store).

Revision ID: 037_user_onboarding_completed_at
Revises: 036_chat_cookie_alert_sent
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '037_user_onboarding_completed_at'
down_revision: Union[str, None] = '036_chat_cookie_alert_sent'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("onboarding_completed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "onboarding_completed_at")
