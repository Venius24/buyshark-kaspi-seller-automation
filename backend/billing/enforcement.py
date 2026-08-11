"""Проверка блокировки сервиса по billing_status магазина. НЕ переиспользует
Store.status='PAUSED' (то поле кодирует намерение оператора и потребляется
list_active_stores(), используемым ~8 воркерами) — отдельная колонка
store_tariffs.billing_status. TTL-кэш ~30с: чат-бот тикает раз в 5с на
магазин, читать БД на каждый тик избыточно."""
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30
_cache: dict = {}  # store_id -> (expires_at, (blocked, reason))


def _cached(store_id: str):
    entry = _cache.get(store_id)
    if entry and entry[0] > time.monotonic():
        return entry[1]
    return None


def bust_cache(store_id: str) -> None:
    _cache.pop(store_id, None)


def is_service_blocked(store_id: str) -> tuple[bool, Optional[str]]:
    cached = _cached(store_id)
    if cached is not None:
        return cached

    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreTariffDB

    session = _get_sync_db_session()
    if not session:
        return (False, None)
    try:
        tariff = session.get(StoreTariffDB, store_id)
        if not tariff:
            result = (False, None)
        elif tariff.billing_status == "ACTIVE":
            result = (False, None)
        else:
            result = (True, tariff.blocked_reason or tariff.billing_status)
    finally:
        session.close()

    _cache[store_id] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
    return result
