"""Общемагазинные выключатели автоматик (демпинг/связка остатков/автосборка)
для вкладки "Глобальное управление". Чат-бот сюда не дублируется — у него
свой ChatbotSettingsDB.enabled (backend/chatbot/config.py).

TTL-кэш ~30с по образцу backend/billing/enforcement.py — фоновые воркеры
(демпинг, авто-списание остатков) читают флаг на каждом тике по каждому
активному стору, читать БД без кэша на каждый тик избыточно.
"""
import time
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30
_cache: dict = {}  # store_id -> (expires_at, {"dumping_enabled": bool, ...})

_FLAG_FIELDS = ("dumping_enabled", "stock_groups_enabled", "autoassembly_enabled")
_DEFAULTS = {name: True for name in _FLAG_FIELDS}


def bust_cache(store_id: str) -> None:
    _cache.pop(store_id, None)


def _cached(store_id: str) -> Optional[dict]:
    entry = _cache.get(store_id)
    if entry and entry[0] > time.monotonic():
        return entry[1]
    return None


def get_flags(store_id: str) -> dict:
    """Читает флаги этого магазина. Отсутствующая строка = все включены (дефолт)."""
    cached = _cached(store_id)
    if cached is not None:
        return cached

    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreFeatureFlagsDB

    session = _get_sync_db_session()
    if not session:
        return dict(_DEFAULTS)
    try:
        row = session.get(StoreFeatureFlagsDB, store_id)
        if row:
            result = {name: getattr(row, name) for name in _FLAG_FIELDS}
        else:
            result = dict(_DEFAULTS)
    finally:
        session.close()

    _cache[store_id] = (time.monotonic() + _CACHE_TTL_SECONDS, result)
    return result


def is_dumping_enabled(store_id: str) -> bool:
    return get_flags(store_id)["dumping_enabled"]


def is_stock_groups_enabled(store_id: str) -> bool:
    return get_flags(store_id)["stock_groups_enabled"]


def is_autoassembly_enabled(store_id: str) -> bool:
    return get_flags(store_id)["autoassembly_enabled"]


def set_flags(store_id: str, **updates) -> dict:
    """Частичный апдейт (только ключи из _FLAG_FIELDS учитываются). Возвращает
    итоговое состояние всех трёх флагов."""
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreFeatureFlagsDB

    changes = {k: v for k, v in updates.items() if k in _FLAG_FIELDS and v is not None}

    session = _get_sync_db_session()
    if not session:
        bust_cache(store_id)
        return dict(_DEFAULTS)
    try:
        row = session.get(StoreFeatureFlagsDB, store_id)
        if not row:
            row = StoreFeatureFlagsDB(store_id=store_id, **{**_DEFAULTS, **changes})
            session.add(row)
        else:
            for k, v in changes.items():
                setattr(row, k, v)
        row.updated_at = datetime.utcnow().isoformat()
        session.commit()
        result = {name: getattr(row, name) for name in _FLAG_FIELDS}
    finally:
        session.close()

    bust_cache(store_id)
    return result
