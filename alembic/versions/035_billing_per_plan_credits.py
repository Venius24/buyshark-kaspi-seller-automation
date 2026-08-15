"""Тарификация: дневной лимит кредитов ответов ИИ переехал с отдельной
надбавки (store_tariffs.credit_addon_code + CREDIT_TIERS-каталог) в
фиксированный лимит, зашитый в сам тариф (владелец, 2026-08-16, ещё до
деплоя слайсов 1-2 на прод — см. HANDOFF.md). START=20/HUNTER=50/
PREDATOR=100/PREDATOR_PLUS=200 в день, ГигаПредприятие — договорной лимит
per-store вместо каталожной ступени.

store_tariffs.credit_addon_code удаляется — концепция сменных ступеней
кредитов ("NONE"/"C70"/"C100") больше не существует. Взамен —
custom_daily_credits_milli (тот же паттерн, что и уже существующий
custom_monthly_price_minor: NULL = взять фиксированный лимит из
catalog.PLANS[plan_code], значение = договорной override для ГигаПредприятие).

Продление тарифа теперь пишет в billing_transactions ДВЕ строки за период
(base/prompt), не три — charge_credit_addon как kind больше не используется
(колонка kind остаётся свободным текстом, менять существующие строки не
нужно — история списаний не переписывается задним числом).

Revision ID: 035_billing_per_plan_credits
Revises: 034_stock_group_deduct_per_unit
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '035_billing_per_plan_credits'
down_revision: Union[str, None] = '034_stock_group_deduct_per_unit'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.add_column(
        "store_tariffs",
        sa.Column("custom_daily_credits_milli", sa.BigInteger(), nullable=True),
    )

    if is_sqlite:
        with op.batch_alter_table("store_tariffs") as batch_op:
            batch_op.drop_column("credit_addon_code")
    else:
        op.drop_column("store_tariffs", "credit_addon_code")


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    op.add_column(
        "store_tariffs",
        sa.Column("credit_addon_code", sa.String(length=20), nullable=False, server_default="NONE"),
    )

    if is_sqlite:
        with op.batch_alter_table("store_tariffs") as batch_op:
            batch_op.drop_column("custom_daily_credits_milli")
    else:
        op.drop_column("store_tariffs", "custom_daily_credits_milli")
