"""Add telegram_links.language_code — Ф5 частичная локализация
(docs/TELEGRAM_BOT_PLAN.md §7.6): Telegram отдаёт language_code на каждый
апдейт, сохраняем при привязке/обращении, используем для выбора ru/kk
подписей главного меню.

Revision ID: 018_telegram_link_language
Revises: 017_telegram_notif_audit
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '018_telegram_link_language'
down_revision: Union[str, None] = '017_telegram_notif_audit'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("telegram_links", sa.Column("language_code", sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.drop_column("telegram_links", "language_code")
