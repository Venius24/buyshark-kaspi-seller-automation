"""chatbot_settings.chat_login_phone — последний номер, по которому прошёл
вход в Kaspi-чат (SMS). Нужен кнопке «Восстановить» прямо в алерте «сессия
чата истекла»: бот сразу запрашивает код на этот номер, без ввода телефона
и без захода в кабинет.

Revision ID: 046_chat_login_phone
Revises: 045_chat_response_log_texts
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '046_chat_login_phone'
down_revision: Union[str, None] = '045_chat_response_log_texts'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chatbot_settings", sa.Column("chat_login_phone", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("chatbot_settings", "chat_login_phone")
