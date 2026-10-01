"""chat_response_logs: тексты вопроса и ответа, заказ, причина.

Журнал качества автоответчика: без текстов нельзя ни посмотреть, что бот
говорит клиентам, ни собрать набор настоящих диалогов для проверки правок
промпта. Эскалации к менеджеру теперь тоже пишутся сюда
(kind=inbound_escalated_trigger / inbound_escalated_ai, billed_units=0).

Revision ID: 045_chat_response_log_texts
Revises: 044_stock_group_sku_recipes
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '045_chat_response_log_texts'
down_revision: Union[str, None] = '044_stock_group_sku_recipes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_response_logs", sa.Column("client_text", sa.Text(), nullable=True))
    op.add_column("chat_response_logs", sa.Column("reply_text", sa.Text(), nullable=True))
    op.add_column("chat_response_logs", sa.Column("order_code", sa.String(length=30), nullable=True))
    op.add_column("chat_response_logs", sa.Column("detail", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_response_logs") as batch_op:
        batch_op.drop_column("detail")
        batch_op.drop_column("order_code")
        batch_op.drop_column("reply_text")
        batch_op.drop_column("client_text")
