"""Тарификация (2026-08-12) — тариф и баланс привязаны к МАГАЗИНУ, не к
пользователю. Новые таблицы: store_tariffs (тарифный профиль + статус
блокировки), store_balances (баланс кабинета, тиын), billing_transactions
(append-only леджер, готов под платёжный шлюз с первого дня — provider/
provider_payment_id/method/idempotency_key), store_daily_credits (дневной
счётчик ответов ИИ, строка создаётся лениво — отсутствие строки и есть
сброс), referral_profiles/store_referrals/referral_accruals (реферальная
система, 50% от реального пополнения приведённого магазина первые 3 месяца).

Плюс: users += is_superadmin (платформенная роль, ортогональна
UserStore.role и referral_profiles) и pending_referral_code (реф-код с
лендинга до появления первого магазина); chat_response_logs +=
credit_units_milli (сверка аудит-лога с быстрым счётчиком).

Существующая per-user Subscription НЕ трогается — она enforce-ит другую ось
(max_stores, квота аккаунта), а не тариф магазина.

Бэкфилл: все существующие магазины → тариф DEV (безлимит, без списаний и
блокировок) с next_charge_at в далёком будущем — воркер и так хардскипает
is_free-планы, это просто подстраховка. Балансы → 0. is_superadmin=true для
owner@example.com. store_bot_profiles.prompt_tier пересчитывается под новые
пороги 500/2000/5000 символов (band0..band3 вместо base/plus/pro/max) —
историю в chat_response_logs.prompt_tier не трогаем, это снапшот на момент
ответа.

INSERT-бэкфиллы написаны с WHERE NOT EXISTS: _get_sync_db_session() вызывает
Base.metadata.create_all() при первом использовании, поэтому на dev-SQLite
таблицы могут появиться раньше, чем отработает эта миграция.

Revision ID: 027_billing_tariffs
Revises: 026_kaspi_quality_metrics_daily
Create Date: 2026-08-12 00:00:00.000000

"""
import datetime as _dt
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '027_billing_tariffs'
down_revision: Union[str, None] = '026_kaspi_quality_metrics_daily'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FAR_FUTURE = _dt.datetime(2100, 1, 1)


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # --- altered existing tables -------------------------------------------------
    op.add_column("users", sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("pending_referral_code", sa.String(length=16), nullable=True))
    op.add_column("chat_response_logs", sa.Column("credit_units_milli", sa.Integer(), nullable=False, server_default="0"))

    # --- store_tariffs -------------------------------------------------------
    op.create_table(
        "store_tariffs",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("plan_code", sa.String(length=30), nullable=False, server_default="DEV"),
        sa.Column("custom_monthly_price_minor", sa.BigInteger(), nullable=True),
        sa.Column("credit_addon_code", sa.String(length=20), nullable=False, server_default="NONE"),
        sa.Column("auto_upgrade_credits", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("billing_status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("blocked_at", sa.DateTime(), nullable=True),
        sa.Column("force_unblock_until", sa.DateTime(), nullable=True),
        sa.Column("billing_anchor_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("next_charge_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id"),
    )
    op.create_index("ix_store_tariffs_next_charge_at", "store_tariffs", ["next_charge_at"])
    if not is_sqlite:
        op.create_foreign_key("fk_store_tariffs_store_id", "store_tariffs", "stores", ["store_id"], ["id"], ondelete="CASCADE")

    # --- store_balances --------------------------------------------------------
    op.create_table(
        "store_balances",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("balance_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id"),
    )
    if not is_sqlite:
        op.create_foreign_key("fk_store_balances_store_id", "store_balances", "stores", ["store_id"], ["id"], ondelete="CASCADE")

    # --- billing_transactions ---------------------------------------------------
    op.create_table(
        "billing_transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_minor", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="completed"),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=True),
        sa.Column("method", sa.String(length=30), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KZT"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_billing_transactions_idempotency_key"),
    )
    op.create_index("ix_billing_transactions_store_id", "billing_transactions", ["store_id"])
    op.create_index("ix_billing_transactions_store_id_created_at", "billing_transactions", ["store_id", "created_at"])
    op.create_index("ix_billing_transactions_provider_payment_id", "billing_transactions", ["provider_payment_id"])
    op.create_index("ix_billing_transactions_created_at", "billing_transactions", ["created_at"])
    if not is_sqlite:
        op.create_foreign_key("fk_billing_transactions_store_id", "billing_transactions", "stores", ["store_id"], ["id"], ondelete="CASCADE")
        op.create_foreign_key("fk_billing_transactions_created_by_user_id", "billing_transactions", "users", ["created_by_user_id"], ["id"], ondelete="SET NULL")

    # --- store_daily_credits -----------------------------------------------------
    op.create_table(
        "store_daily_credits",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("limit_milli", sa.BigInteger(), nullable=False),
        sa.Column("used_milli", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("exhausted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("store_id", "date"),
    )
    if not is_sqlite:
        op.create_foreign_key("fk_store_daily_credits_store_id", "store_daily_credits", "stores", ["store_id"], ["id"], ondelete="CASCADE")

    # --- referral_profiles -------------------------------------------------------
    op.create_table(
        "referral_profiles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("commission_percent", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("commission_months", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("code", name="uq_referral_profiles_code"),
    )
    op.create_index("ix_referral_profiles_code", "referral_profiles", ["code"])
    if not is_sqlite:
        op.create_foreign_key("fk_referral_profiles_user_id", "referral_profiles", "users", ["user_id"], ["id"], ondelete="CASCADE")

    # --- store_referrals -----------------------------------------------------
    op.create_table(
        "store_referrals",
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("referrer_user_id", sa.String(length=36), nullable=False),
        sa.Column("referral_code", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("assigned_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("store_id"),
    )
    op.create_index("ix_store_referrals_referrer_user_id", "store_referrals", ["referrer_user_id"])
    if not is_sqlite:
        op.create_foreign_key("fk_store_referrals_store_id", "store_referrals", "stores", ["store_id"], ["id"], ondelete="CASCADE")
        op.create_foreign_key("fk_store_referrals_referrer_user_id", "store_referrals", "users", ["referrer_user_id"], ["id"], ondelete="RESTRICT")
        op.create_foreign_key("fk_store_referrals_assigned_by_user_id", "store_referrals", "users", ["assigned_by_user_id"], ["id"], ondelete="SET NULL")

    # --- referral_accruals ---------------------------------------------------
    op.create_table(
        "referral_accruals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("referrer_user_id", sa.String(length=36), nullable=False),
        sa.Column("store_id", sa.String(length=36), nullable=False),
        sa.Column("source_transaction_id", sa.String(length=36), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="accrued"),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("payout_ref", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_transaction_id", name="uq_referral_accruals_source_transaction_id"),
    )
    op.create_index("ix_referral_accruals_referrer_user_id", "referral_accruals", ["referrer_user_id"])
    op.create_index("ix_referral_accruals_store_id", "referral_accruals", ["store_id"])
    op.create_index("ix_referral_accruals_created_at", "referral_accruals", ["created_at"])
    if not is_sqlite:
        op.create_foreign_key("fk_referral_accruals_referrer_user_id", "referral_accruals", "users", ["referrer_user_id"], ["id"], ondelete="RESTRICT")
        op.create_foreign_key("fk_referral_accruals_store_id", "referral_accruals", "stores", ["store_id"], ["id"], ondelete="CASCADE")
        op.create_foreign_key("fk_referral_accruals_source_transaction_id", "referral_accruals", "billing_transactions", ["source_transaction_id"], ["id"], ondelete="CASCADE")

    # --- бэкфилл ---------------------------------------------------------------
    now_str = _now_literal(bind)

    bind.execute(sa.text(
        """
        INSERT INTO store_tariffs (store_id, plan_code, custom_monthly_price_minor,
                                    credit_addon_code, auto_upgrade_credits, billing_status,
                                    billing_anchor_day, period_start, period_end,
                                    next_charge_at, activated_at, created_at, updated_at)
        SELECT s.id, 'DEV', NULL, 'NONE', false, 'ACTIVE', 1,
               :now, :far, :far, :now, :now, :now
        FROM stores s
        WHERE NOT EXISTS (SELECT 1 FROM store_tariffs t WHERE t.store_id = s.id)
        """
    ), {"now": now_str, "far": _FAR_FUTURE})

    bind.execute(sa.text(
        """
        INSERT INTO store_balances (store_id, balance_minor, updated_at)
        SELECT s.id, 0, :now
        FROM stores s
        WHERE NOT EXISTS (SELECT 1 FROM store_balances b WHERE b.store_id = s.id)
        """
    ), {"now": now_str})

    bind.execute(sa.text(
        "UPDATE users SET is_superadmin = 1 WHERE lower(email) = 'owner@example.com'"
        if is_sqlite else
        "UPDATE users SET is_superadmin = true WHERE lower(email) = 'owner@example.com'"
    ))

    # store_bot_profiles может не существовать таблицы contents если её ещё
    # не создали — но она есть с ревизии 019, так что просто пересчитываем.
    bind.execute(sa.text(
        """
        UPDATE store_bot_profiles SET prompt_tier = CASE
          WHEN length(coalesce(custom_instructions, '')) <= 500 THEN 'band0'
          WHEN length(coalesce(custom_instructions, '')) <= 2000 THEN 'band1'
          WHEN length(coalesce(custom_instructions, '')) <= 5000 THEN 'band2'
          ELSE 'band3' END
        """
    ))


def _now_literal(bind) -> _dt.datetime:
    """Единый Python datetime для :now, чтобы store_tariffs.period_start и
    store_balances.updated_at были согласованы в один и тот же момент вставки
    независимо от диалекта (избегаем func.now() внутри именованного bind-параметра,
    т.к. sa.text() с :now ожидает конкретное значение, а не серверное выражение).
    Раньше возвращалась строка — SQLite коерсит её молча, asyncpg на Postgress
    требует настоящий datetime.datetime для TIMESTAMP-колонок и падает с
    DataError (поймано вживую на проде при первом накате на реальный Postgres)."""
    return _dt.datetime.utcnow()


def downgrade() -> None:
    op.drop_table("referral_accruals")
    op.drop_table("store_referrals")
    op.drop_table("referral_profiles")
    op.drop_table("store_daily_credits")
    op.drop_table("billing_transactions")
    op.drop_table("store_balances")
    op.drop_table("store_tariffs")

    with op.batch_alter_table("chat_response_logs") as batch_op:
        batch_op.drop_column("credit_units_milli")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("pending_referral_code")
        batch_op.drop_column("is_superadmin")
