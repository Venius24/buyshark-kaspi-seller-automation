"""Add chatbot_settings.chat_cookie_alert_sent — персистит анти-спам флаг
"уже уведомили о протухшей чат-сессии" (раньше in-memory dict, обнулялся на
каждом рестарте процесса/деплое, поэтому нерешённая проблема переуведомлялась
на каждый деплой — инцидент 2026-08-22).

Revision ID: 036_chat_cookie_alert_sent
Revises: 035_billing_per_plan_credits
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '036_chat_cookie_alert_sent'
down_revision: Union[str, None] = '035_billing_per_plan_credits'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chatbot_settings",
        sa.Column("chat_cookie_alert_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("chatbot_settings", "chat_cookie_alert_sent")
