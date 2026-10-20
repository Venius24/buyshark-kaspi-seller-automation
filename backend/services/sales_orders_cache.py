"""
Кэш разобранных `sales_orders` в памяти процесса.

Дашборд опрашивает прогноз остатков (`stock_forecast.load_daily_sales`) и
сводку продаж (`state_helpers._load_sales_summary`) с каждого открытого
клиента, и обе ручки каждый раз заново тянули из БД и прогоняли через
`json.loads` десятки тысяч `entries_payload`. У Бархана это ~0,35 с CPU под
GIL на вызов прогноза и ~0,2 с на сводку за 30 дней (замер 2026-10-18), то
есть секунды простоя event loop на каждый круг опроса.

Почему кэш строк, а не готовых ответов и не предрасчёт в БД:

* Сами заказы меняются только в одном месте — `_upsert_sales_orders`, его
  зовёт `sales_sync_worker` раз в 30 минут в этом же процессе (uvicorn
  `--workers 1`). Он после коммита вызывает `bump(store_id)`, и следующий
  запрос перечитывает БД. Поэтому ответы те же, что без кэша, а не «на N
  минут старее».
* Окно ответов скользит (`utcnow() - days`), каталог и остатки живут своей
  жизнью — кэш готового ответа был бы побайтно другим ответом. А строки
  режем по `created_at` на каждом запросе, остальной расчёт идёт как раньше.
* Дневной предрасчёт по SKU при синке — новая таблица, миграция и бэкафилл
  ради того же выигрыша, плюс второй источник правды о продажах.

TTL — только страховка от записи в `sales_orders` из другого процесса
(ручной скрипт на сервере): через `_TTL_SECONDS` кэш перечитается сам.

Отдаваемые строки общие для всех вызывающих — их нельзя менять на месте.
"""

import bisect
import json
import threading
import time
from datetime import datetime
from typing import NamedTuple

_TTL_SECONDS = 10 * 60


class SalesEntry(NamedTuple):
    """Позиция заказа — сырые значения из entries_payload, без нормализации:
    у прогноза и у сводки свои правила (strip, `or 0`, int())."""
    sku: object
    name: object
    quantity: object
    total_price: object


class SalesRow(NamedTuple):
    created_at: datetime
    total_price: float
    entries: tuple  # tuple[SalesEntry, ...]; () если payload не разобрался


class _StoreCache(NamedTuple):
    generation: int
    loaded_since: datetime
    expires_at: float
    rows: list  # list[SalesRow], по возрастанию created_at
    keys: list  # created_at тех же строк — для bisect
    derived: dict  # (имя, since) -> результат derived(); живёт, пока жива запись


_lock = threading.Lock()
_generation: dict = {}
_cache: dict = {}


def bump(store_id: str) -> None:
    """Сбросить кэш магазина: заказы в БД поменялись."""
    with _lock:
        _generation[store_id] = _generation.get(store_id, 0) + 1
        _cache.pop(store_id, None)


def clear() -> None:
    with _lock:
        _cache.clear()


def _parse_entries(payload: str) -> tuple:
    try:
        entries = json.loads(payload)
    except Exception:
        return ()
    return tuple(
        SalesEntry(e.get("sku"), e.get("name"), e.get("quantity"), e.get("totalPrice"))
        for e in entries
    )


def _load(store_id: str, since: datetime) -> list:
    from sqlalchemy import select

    from backend.db.models import SalesOrderDB
    from backend.state_helpers import _get_sync_db_session

    session = _get_sync_db_session()
    if not session:
        return []
    try:
        rows = session.execute(
            select(SalesOrderDB.created_at, SalesOrderDB.total_price, SalesOrderDB.entries_payload)
            .where(SalesOrderDB.store_id == store_id, SalesOrderDB.created_at >= since)
            .order_by(SalesOrderDB.created_at.asc(), SalesOrderDB.order_internal_id.asc())
        ).all()
        return [SalesRow(c, p, _parse_entries(e)) for c, p, e in rows]
    finally:
        session.close()


def _entry(store_id: str, since: datetime) -> _StoreCache:
    with _lock:
        generation = _generation.get(store_id, 0)
        cached = _cache.get(store_id)
    if (cached is None or cached.generation != generation
            or cached.expires_at <= time.monotonic() or cached.loaded_since > since):
        load_since = since if cached is None else min(since, cached.loaded_since)
        rows = _load(store_id, load_since)
        cached = _StoreCache(generation, load_since, time.monotonic() + _TTL_SECONDS,
                             rows, [r.created_at for r in rows], {})
        with _lock:
            # Если пока мы читали, синк успел вызвать bump(), прочитанное уже
            # могло устареть — отдать можно (запрос начался раньше записи),
            # но класть в кэш нельзя.
            if _generation.get(store_id, 0) == generation:
                _cache[store_id] = cached
    return cached


def _slice(cached: _StoreCache, since: datetime) -> list:
    return cached.rows[bisect.bisect_left(cached.keys, since):]


def orders_since(store_id: str, since: datetime) -> list:
    """Заказы магазина с `created_at >= since` по возрастанию created_at —
    то же, что вернул бы SELECT, только без повторного разбора JSON."""
    return _slice(_entry(store_id, since), since)


def derived(store_id: str, since: datetime, name: str, build):
    """`build(orders_since(store_id, since))`, посчитанный один раз на версию
    кэша. Для агрегатов, которые зависят только от строк и `since` (дневные
    продажи прогноза), — иначе каждый опрос заново гоняет Python-цикл по
    десяткам тысяч строк. Результат общий: менять его на месте нельзя."""
    cached = _entry(store_id, since)
    key = (name, since)
    with _lock:
        if key in cached.derived:
            return cached.derived[key]
    value = build(_slice(cached, since))
    with _lock:
        if len(cached.derived) >= 32:
            cached.derived.clear()
        cached.derived[key] = value
    return value
