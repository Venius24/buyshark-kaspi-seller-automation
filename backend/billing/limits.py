"""Подсчёт товаров против лимита тарифа. Считаются ТОЛЬКО активные в продаже
(product["status"] == "Активен", см. backend/kaspi_wrapper.py:1051) — снятые
с продажи и архивные не идут в лимит (решение владельца, 2026-08-12).

Возвращает None, если состояние неизвестно (StoreProductDB ещё нет, или
устарело дольше суток) — неизвестно НЕ значит "соответствует лимиту" и НЕ
значит "нарушение", это просто "нельзя судить сейчас". Новый магазин читает
0 товаров до первой синхронизации (products_sync_worker, тик 1800с) — его
нельзя блокировать по пустому кэшу.
"""
import datetime as _dt
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_STALE_AFTER_HOURS = 24


def count_billable_products(store_id: str) -> Optional[int]:
    from backend.state_helpers import _get_sync_db_session, _load_products
    from backend.db.models import StoreProductDB

    session = _get_sync_db_session()
    if not session:
        return None
    try:
        row = session.get(StoreProductDB, store_id)
        if not row:
            return None
        updated_at = getattr(row, "updated_at", None)
        if updated_at and (_dt.datetime.utcnow() - updated_at) > _dt.timedelta(hours=_STALE_AFTER_HOURS):
            return None
    finally:
        session.close()

    products = _load_products(store_id)
    return sum(1 for p in products if p.get("status") == "Активен")
