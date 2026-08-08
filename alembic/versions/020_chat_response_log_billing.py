"""Extend chat_response_logs with billing fields (Фаза 2 — тарификация чат-бота).

Каждая запись раньше была просто "был авто-ответ" (для heatmap активности).
Теперь различаем kind (реальный вызов ИИ vs бесплатный логистический шаблон
vs будущие исходящие рассылки Фазы 3-5), фиксируем tier/model/токены на
момент ответа (тариф магазина может измениться позже — история не должна
"переписываться") и billed_units — сколько это стоило по счётчику
(логистический шаблон = 0, т.к. не дергает DeepSeek).

Revision ID: 020_chat_response_log_billing
Revises: 019_store_bot_profiles
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '020_chat_response_log_billing'
down_revision: Union[str, None] = '019_store_bot_profiles'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_response_logs", sa.Column("kind", sa.String(length=30), nullable=False, server_default="inbound_reply"))
    op.add_column("chat_response_logs", sa.Column("prompt_tier", sa.String(length=20), nullable=False, server_default=""))
    op.add_column("chat_response_logs", sa.Column("model", sa.String(length=50), nullable=False, server_default=""))
    op.add_column("chat_response_logs", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    op.add_column("chat_response_logs", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    op.add_column("chat_response_logs", sa.Column("billed_units", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("chat_response_logs") as batch_op:
        batch_op.drop_column("billed_units")
        batch_op.drop_column("completion_tokens")
        batch_op.drop_column("prompt_tokens")
        batch_op.drop_column("model")
        batch_op.drop_column("prompt_tier")
        batch_op.drop_column("kind")
