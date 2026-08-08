"""Add store_bot_profiles — per-store chat-bot system prompt profile.

Рефакторинг чат-бота (2026-08-08): систем-промпт был хардкодом под BARKHAN
прямо в backend/chatbot/ai.py (имя ИИ-менеджера, название магазина, адрес
самовывоза, график, ассортимент) — второй магазин, включивший бота, получил
бы чужой адрес и график. Профиль вынесен в БД, промпт теперь собирается
шаблоном (backend/chatbot/prompt.py) с подстановкой полей этого магазина +
опциональным доп.-блоком инструкций от владельца.

Revision ID: 019_store_bot_profiles
Revises: 018_telegram_link_language
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '019_store_bot_profiles'
down_revision: Union[str, None] = '018_telegram_link_language'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.create_table(
        "store_bot_profiles",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("profile_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("custom_instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("custom_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("prompt_tier", sa.String(length=20), nullable=False, server_default="base"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id"),
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_store_bot_profiles_store_id", "store_bot_profiles", "stores", ["store_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_table("store_bot_profiles")
