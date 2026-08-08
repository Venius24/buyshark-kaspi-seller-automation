"""Add outbound_queue.replied_at — статистика рассылок (Фаза 6).

Записывает, ответил ли клиент ПОСЛЕ того, как ему ушло исходящее сообщение
кампании (пофиг, каким путём в итоге обработался его ответ — human_triggers/
логистика/ИИ, см. backend/chatbot/worker.py::_run_chatbot_pass). Нужно для
честной метки "доля ответивших" (backend/chatbot/outbound.py::mark_replied_if_pending)
вместо приблизительного вывода через chat_response_logs, который бы пропускал
ответы, ушедшие в HUMAN_OVERRIDE (там лог не пишется).

Revision ID: 023_outbound_stats
Revises: 022_review_campaign
Create Date: 2026-08-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '023_outbound_stats'
down_revision: Union[str, None] = '022_review_campaign'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "outbound_queue",
        sa.Column("replied_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outbound_queue", "replied_at")
