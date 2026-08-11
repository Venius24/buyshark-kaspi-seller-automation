"""Дневные кредиты ответов ИИ-бота — учёт через compare-and-swap на одну
строку store_daily_credits. Строка создаётся ЛЕНИВО при первом расходе за
день; отсутствие строки и есть сброс — отдельного job'а на полночь не
существует. "День" считается по Алматы (catalog.BILLING_TZ_OFFSET_HOURS),
не по UTC, иначе кредиты обнулялись бы в 05:00 по местному времени.
"""
import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from backend.billing import catalog

logger = logging.getLogger(__name__)


def today_key(now: Optional[_dt.datetime] = None) -> str:
    """'YYYY-MM-DD' по часовому поясу Алматы, из UTC-времени now (или
    текущего). Все datetime в проекте — naive UTC (datetime.utcnow())."""
    now = now or _dt.datetime.utcnow()
    local = now + _dt.timedelta(hours=catalog.BILLING_TZ_OFFSET_HOURS)
    return local.strftime("%Y-%m-%d")


@dataclass
class ConsumeResult:
    ok: bool
    used_milli: int = 0
    limit_milli: int = 0


def _daily_limit_milli(store_id: str, session) -> int:
    from backend.db.models import StoreTariffDB
    tariff = session.get(StoreTariffDB, store_id)
    if not tariff:
        return catalog.effective_daily_credits(catalog.DEFAULT_CREDIT_ADDON)
    if catalog.is_free_plan(tariff.plan_code):
        # DEV — практически безлимит; крупное число вместо None, чтобы
        # арифметика CAS оставалась целочисленной без спецкейсов.
        return 10 ** 12
    return catalog.effective_daily_credits(tariff.credit_addon_code)


def try_consume(store_id: str, units_milli: int, now: Optional[_dt.datetime] = None) -> ConsumeResult:
    """Атомарно резервирует units_milli кредитов на сегодня. Возвращает
    ok=False, если это превысило бы дневной лимит — ничего не меняется."""
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreDailyCreditDB

    session = _get_sync_db_session()
    if not session:
        # Не можем проверить БД — не блокируем сервис из-за инфраструктурного
        # сбоя, но и не занижаем счётчик задним числом.
        return ConsumeResult(ok=True)
    try:
        date = today_key(now)
        limit_milli = _daily_limit_milli(store_id, session)

        row = session.get(StoreDailyCreditDB, (store_id, date))
        if not row:
            session.execute(text(
                "INSERT INTO store_daily_credits (store_id, date, limit_milli, used_milli) "
                "SELECT :store_id, :date, :limit_milli, 0 "
                "WHERE NOT EXISTS (SELECT 1 FROM store_daily_credits WHERE store_id = :store_id AND date = :date)"
            ), {"store_id": store_id, "date": date, "limit_milli": limit_milli})
            session.commit()

        result = session.execute(text(
            "UPDATE store_daily_credits SET used_milli = used_milli + :n "
            "WHERE store_id = :store_id AND date = :date AND used_milli + :n <= limit_milli"
        ), {"store_id": store_id, "date": date, "n": units_milli})
        session.commit()

        row = session.get(StoreDailyCreditDB, (store_id, date))
        if not row:
            return ConsumeResult(ok=False)
        if result.rowcount and result.rowcount > 0:
            if row.used_milli >= row.limit_milli:
                row.exhausted_at = row.exhausted_at or _dt.datetime.utcnow()
                session.commit()
            return ConsumeResult(ok=True, used_milli=row.used_milli, limit_milli=row.limit_milli)
        return ConsumeResult(ok=False, used_milli=row.used_milli, limit_milli=row.limit_milli)
    except Exception as e:
        logger.error(f"[billing.credits] try_consume failed for {store_id}: {e}")
        session.rollback()
        return ConsumeResult(ok=True)  # инфраструктурный сбой — не блокируем сервис
    finally:
        session.close()


def release(store_id: str, units_milli: int, now: Optional[_dt.datetime] = None) -> None:
    """Возвращает units_milli кредитов (например, после неудачной отправки
    сообщения). Никогда не уводит used_milli ниже нуля."""
    from backend.state_helpers import _get_sync_db_session

    session = _get_sync_db_session()
    if not session:
        return
    try:
        date = today_key(now)
        session.execute(text(
            "UPDATE store_daily_credits SET used_milli = MAX(0, used_milli - :n) "
            "WHERE store_id = :store_id AND date = :date"
        ), {"store_id": store_id, "date": date, "n": units_milli})
        session.commit()
    except Exception as e:
        logger.error(f"[billing.credits] release failed for {store_id}: {e}")
        session.rollback()
    finally:
        session.close()


def today_usage(store_id: str, now: Optional[_dt.datetime] = None) -> ConsumeResult:
    """Только для чтения (кабинет/summary) — не создаёт строку, если её ещё
    нет (это значит "сегодня ничего не потрачено")."""
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreDailyCreditDB

    session = _get_sync_db_session()
    if not session:
        return ConsumeResult(ok=True)
    try:
        date = today_key(now)
        row = session.get(StoreDailyCreditDB, (store_id, date))
        if not row:
            return ConsumeResult(ok=True, used_milli=0, limit_milli=_daily_limit_milli(store_id, session))
        return ConsumeResult(ok=row.used_milli < row.limit_milli, used_milli=row.used_milli, limit_milli=row.limit_milli)
    finally:
        session.close()
