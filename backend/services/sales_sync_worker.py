import asyncio
import datetime
import logging

from backend.kaspi_wrapper import KaspiAPI
from backend.state_helpers import _upsert_sales_orders, _get_latest_sales_order_date
from backend.security.store_context import list_active_stores

logger = logging.getLogger(__name__)

# Kaspi отдаёт максимум 14 дней за один запрос (см. KaspiAPI._date_range) —
# используем 13-дневные окна с запасом. Первый бэкафилл на новом сторе
# уходит на этот срок назад; после этого воркер знает дату последнего
# сохранённого заказа и досканирует только недостающий хвост.
_WINDOW_DAYS = 13
_BACKFILL_DAYS = 90


def _windows_since(since: datetime.datetime, now: datetime.datetime) -> list:
    """Режет [since, now] на последовательные <=13-дневные окна (ge_ms, le_ms)."""
    windows = []
    cursor = since
    while cursor < now:
        window_end = min(cursor + datetime.timedelta(days=_WINDOW_DAYS), now)
        windows.append((
            int(cursor.timestamp() * 1000),
            int(window_end.timestamp() * 1000),
        ))
        cursor = window_end
    return windows


async def _sync_store_sales(token: str, store_id: str) -> int:
    now = datetime.datetime.utcnow()
    latest = await asyncio.to_thread(_get_latest_sales_order_date, store_id)
    # Пересканируем последний день заново на случай заказов, изменивших статус
    # на COMPLETED уже после нашего предыдущего прохода.
    since = (latest - datetime.timedelta(days=1)) if latest else (now - datetime.timedelta(days=_BACKFILL_DAYS))

    total_new = 0
    for ge_ms, le_ms in _windows_since(since, now):
        orders = await asyncio.to_thread(KaspiAPI.get_completed_orders, token, ge_ms, le_ms)
        if orders:
            total_new += await asyncio.to_thread(_upsert_sales_orders, orders, store_id)

    if total_new:
        logger.info(f"💰 [Sales Sync Worker] Стор {store_id}: сохранено {total_new} новых заказов.")
    return total_new


async def run_sales_sync_cycle():
    stores = await list_active_stores()
    for store in stores:
        try:
            await _sync_store_sales(store.token, store.store_id)
        except Exception as e:
            logger.error(f"❌ [Sales Sync Worker] Ошибка синхронизации продаж для стора {store.store_id}: {e}")


async def start_sales_sync_loop(interval_seconds: int = 1800):
    """Фоновый воркер, подтягивающий реальную историю завершённых заказов
    (для настоящей статистики выручки/продаж) каждые 30 минут по умолчанию."""
    logger.info(f"💰 [Sales Sync Worker] Фоновый воркер синхронизации продаж запущен (интервал: {interval_seconds}s).")

    await asyncio.sleep(20)

    while True:
        try:
            await run_sales_sync_cycle()
        except Exception as e:
            logger.error(f"❌ [Sales Sync Worker] Ошибка цикла синхронизации продаж: {e}")

        await asyncio.sleep(interval_seconds)
