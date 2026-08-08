"""Add chat_states.awaiting_review_reply — кампания «Отзыв» (Фаза 5).

Одноразовый флаг: ставится, когда исходящее сообщение campaign='review'
реально отправлено клиенту (backend/chatbot/outbound.py), и подмешивается в
СЛЕДУЮЩИЙ ответ ИИ на этот чат (backend/chatbot/prompt.py::render_system_prompt),
чтобы модель могла распознать негативный ответ на просьбу об отзыве и
эскалировать через существующую метку [HUMAN_OVERRIDE]. Снимается сразу же,
как только приходит первый ответ клиента после отправки — флаг именно
"следующий ответ", а не "любой ответ, пока не снимут".

Revision ID: 022_review_campaign
Revises: 021_outbound_queue
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '022_review_campaign'
down_revision: Union[str, None] = '021_outbound_queue'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_states",
        sa.Column("awaiting_review_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("chat_states", "awaiting_review_reply")
