"""Движок биллинга: продление подписки (charge_period) и оценка блокировки
(evaluate_block). Обе функции синхронные, вызываются из billing_worker.py
через asyncio.to_thread(), либо напрямую из sync-роутеров (например,
"Начислить сейчас" в админке).
"""
import datetime as _dt
import logging
from typing import Optional

from backend.billing import catalog, credits, ledger, limits

logger = logging.getLogger(__name__)

# Не догоняем больше N просроченных периодов за один вызов — если воркер был
# выключен полгода, не хотим за один тик списать 6 месяцев без предупреждения.
_MAX_CATCHUP_PERIODS = 6


def _clamp_day(year: int, month: int, day: int) -> int:
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    return min(day, last_day)


def _add_month(dt: _dt.datetime, anchor_day: int) -> _dt.datetime:
    """Следующий месяц, день клампится по длине месяца (31 янв -> 28/29 фев,
    не "+30 дней" — иначе якорный день дрейфует со временем."""
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    day = _clamp_day(year, month, anchor_day)
    return dt.replace(year=year, month=month, day=day)


def charge_period(store_id: str, now: Optional[_dt.datetime] = None) -> int:
    """Списывает продление за все просроченные периоды (обычно один).
    Возвращает число реально начисленных периодов. DEV/GIGA-без-цены и
    прочие is_free планы никогда сюда попадать не должны — вызывающий
    (billing_worker) хардскипает их до вызова."""
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreTariffDB

    now = now or _dt.datetime.utcnow()
    charged_periods = 0

    for _ in range(_MAX_CATCHUP_PERIODS):
        session = _get_sync_db_session()
        if not session:
            return charged_periods
        try:
            tariff = session.get(StoreTariffDB, store_id)
            if not tariff or catalog.is_free_plan(tariff.plan_code):
                return charged_periods
            if tariff.next_charge_at > now:
                return charged_periods

            period_start = tariff.period_start
            period_key = period_start.strftime("%Y-%m-%d")

            base_price = catalog.plan_price_minor(tariff.plan_code, tariff.custom_monthly_price_minor)
            credit_price = catalog.credit_addon_price_minor(tariff.credit_addon_code)
            prompt_band_code = _current_prompt_band(store_id)
            prompt_price = catalog.PROMPT_BANDS_BY_CODE.get(prompt_band_code, catalog.PROMPT_BANDS_BY_CODE[catalog.DEFAULT_PROMPT_BAND]).monthly_price_minor

            lines = [
                ("charge_subscription", base_price, "base"),
                ("charge_credit_addon", credit_price, "credits"),
                ("charge_prompt_addon", prompt_price, "prompt"),
            ]
            for kind, amount, line in lines:
                if amount <= 0:
                    continue
                key = f"sub:{store_id}:{period_key}:{line}"
                result = ledger.debit(
                    store_id, amount, kind, idempotency_key=key,
                    period_start=period_start, period_end=tariff.period_end,
                )
                if result.applied:
                    _maybe_accrue_referral_on_topup_only_noop()  # см. docstring ниже

            anchor = tariff.billing_anchor_day
            new_start = tariff.period_end
            new_end = _add_month(new_start, anchor)

            tariff.period_start = new_start
            tariff.period_end = new_end
            tariff.next_charge_at = new_end
            tariff.updated_at = _dt.datetime.utcnow()
            session.commit()
            charged_periods += 1
        except Exception as e:
            logger.error(f"[billing.engine] charge_period failed for {store_id}: {e}")
            session.rollback()
            return charged_periods
        finally:
            session.close()

    return charged_periods


def _maybe_accrue_referral_on_topup_only_noop():
    """Реферальная комиссия начисляется ТОЛЬКО при реальном пополнении
    (topup_*), не при списании за тариф (charge_*) — решение владельца
    2026-08-12. См. backend/billing/referrals.py::accrue_on_topup(), которая
    вызывается из ledger.credit() при topup, не отсюда. Функция-заглушка
    оставлена как явный маркер того, что списания за тариф НЕ начисляют
    комиссию — на случай, если это решение попытаются "исправить" позже."""
    return None


def _current_prompt_band(store_id: str) -> str:
    from backend.chatbot.prompt import get_profile
    profile = get_profile(store_id)
    return profile.get("prompt_tier") or catalog.DEFAULT_PROMPT_BAND


def evaluate_block(store_id: str, now: Optional[_dt.datetime] = None) -> str:
    """Чистая функция состояния -> billing_status, идемпотентна по
    построению (устанавливает значение, а не применяет дельту). DEV
    хардскипается вызывающим (billing_worker) до вызова — но и здесь
    подстраховка на случай прямого вызова (например, из админки)."""
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreTariffDB
    from backend.billing import enforcement

    now = now or _dt.datetime.utcnow()

    session = _get_sync_db_session()
    if not session:
        return "ACTIVE"
    try:
        tariff = session.get(StoreTariffDB, store_id)
        if not tariff:
            return "ACTIVE"
        if catalog.is_free_plan(tariff.plan_code):
            if tariff.billing_status != "ACTIVE":
                tariff.billing_status = "ACTIVE"
                tariff.blocked_reason = None
                tariff.blocked_at = None
                session.commit()
                enforcement.bust_cache(store_id)
            return "ACTIVE"

        if tariff.force_unblock_until and tariff.force_unblock_until > now:
            new_status, reason = "ACTIVE", None
        else:
            bal = ledger.balance(store_id)
            plan = catalog.get_plan(tariff.plan_code)
            product_count = limits.count_billable_products(store_id)
            over_limit = (
                plan.max_products is not None
                and product_count is not None
                and product_count > plan.max_products
            )
            if bal <= catalog.BALANCE_BLOCK_THRESHOLD_MINOR:
                new_status, reason = "BLOCKED_BALANCE", f"Баланс {bal/100:.0f} ₸ достиг порога блокировки"
            elif over_limit:
                new_status, reason = "BLOCKED_LIMIT", f"Товаров в продаже {product_count} превышает лимит тарифа {plan.max_products}"
            else:
                new_status, reason = "ACTIVE", None

        if new_status != tariff.billing_status:
            tariff.billing_status = new_status
            tariff.blocked_reason = reason
            tariff.blocked_at = now if new_status != "ACTIVE" else None
            session.commit()
            enforcement.bust_cache(store_id)
            _notify_block_transition(store_id, new_status, reason)
        return new_status
    except Exception as e:
        logger.error(f"[billing.engine] evaluate_block failed for {store_id}: {e}")
        session.rollback()
        return "ACTIVE"
    finally:
        session.close()


def _notify_block_transition(store_id: str, new_status: str, reason: Optional[str]) -> None:
    """Узкий алерт "тариф заблокирован/разблокирован" через существующий
    Telegram-диспетчер (группа 'money' — тот же канал, что финансовые
    уведомления). Уведомления не должны ронять цикл биллинга при сбое."""
    try:
        from backend.chatbot.notify_dispatch import dispatch
        import asyncio
        msg = (
            "🔓 Магазин разблокирован (тарификация)" if new_status == "ACTIVE"
            else f"🔒 Магазин заблокирован: {reason}"
        )
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(dispatch(store_id, "money", msg, urgent=True))
    except Exception as e:
        logger.debug(f"[billing.engine] notify_block_transition skipped: {e}")
