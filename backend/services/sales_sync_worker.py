import asyncio
import datetime
import logging

from backend.kaspi_wrapper import KaspiAPI
from backend.state_helpers import _upsert_sales_orders, _get_latest_sales_order_date, resolve_external_code
from backend.security.store_context import list_active_stores
from backend.chatbot.review_campaign import scan_and_enqueue_review_campaign
from backend.services.webhook_dispatcher import dispatch_webhook_event

logger = logging.getLogger(__name__)

# Kaspi отдаёт максимум 14 дней за один запрос (см. KaspiAPI._date_range) —
# используем 13-дневные окна с запасом. Первый бэкафилл на новом сторе
# уходит на этот срок назад; после этого воркер знает дату последнего
# сохранённого заказа и досканирует только недостающий хвост.
#
# Фильтр `creationDate` для ARCHIVE/COMPLETED на деле срабатывает по
# completionDate (замер 08.10.2026 на Бархане: окно за 25.09 отдало заказы,
# закрытые 25.09, а созданные 17–24.09). Поэтому окна обязаны доходить до
# текущего момента: окно, кончающееся в прошлом, не увидит заказы, закрытые
# позже. Сверка 20.09–08.10 с полным пересканом: недосчёта нет.
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


async def _notify_order_completed(order: dict, store_id: str):
    """[Public API 1С/ERP, черновик] Отправляет order.completed через тот же
    dispatch_webhook_event, что и stock.updated (HMAC + 3 ретрая). Схема
    пейлоада черновая — комиссия/НДС/статус выплаты сюда сознательно не
    включены, см. GET /api/v1/public/orders (та же сериализация)."""
    entries = []
    for e in order.get("entries") or []:
        sku = e.get("sku")
        merchant_sku = e.get("merchantSku")
        entries.append({
            "sku": sku,
            "merchant_sku": merchant_sku,
            "external_code": await asyncio.to_thread(resolve_external_code, store_id, sku, merchant_sku),
            "name": e.get("name"),
            "qty": e.get("qty"),
            "total_price": e.get("totalPrice"),
        })

    await dispatch_webhook_event(
        "order.completed",
        {
            "order_internal_id": order.get("internalId"),
            "order_code": order.get("code"),
            "total_price": order.get("totalPrice"),
            "status": order.get("status"),
            "state": order.get("state"),
            "entries": entries,
        },
        store_id,
    )


async def _sync_store_sales(token: str, store_id: str) -> int:
    now = datetime.datetime.utcnow()
    latest = await asyncio.to_thread(_get_latest_sales_order_date, store_id)
    # Холодный старт (новый стор, ещё нет ни одного сохранённого заказа) —
    # уходим в 90-дневный бэкафилл. Заказы этого прохода вставляются с
    # review_eligible=False (см. state_helpers._upsert_sales_orders) — иначе
    # ежедневный скан кампании «Отзыв» (review_campaign.py) поймал бы всю
    # историю разом при первом же подключении стора. Со следующего тика
    # latest уже известен, и обычные заказы вставляются review_eligible=True.
    is_cold_start = latest is None
    # Пересканируем последний день заново на случай заказов, изменивших статус
    # на COMPLETED уже после нашего предыдущего прохода.
    since = (latest - datetime.timedelta(days=1)) if latest else (now - datetime.timedelta(days=_BACKFILL_DAYS))

    total_new = 0
    for ge_ms, le_ms in _windows_since(since, now):
        orders = await asyncio.to_thread(KaspiAPI.get_completed_orders, token, ge_ms, le_ms)
        if not orders:
            continue
        new_orders = await asyncio.to_thread(_upsert_sales_orders, orders, store_id, not is_cold_start)
        total_new += len(new_orders)

        # Событие для 1С/ERP (Слайс 3 webhook), только на реально новых заказах
        # и не на холодном 90-дневном бэкафилле — иначе первое подключение
        # стора разом шлёт сотни вебхуков во внешнюю учётку. Ошибка нотификации
        # никогда не должна ронять сам синк остатков/продаж.
        if new_orders and not is_cold_start:
            for o in new_orders:
                try:
                    await _notify_order_completed(o, store_id)
                except Exception as e:
                    logger.warning(f"⚠️ [Sales Sync Worker] Не удалось отправить order.completed для {o.get('code')}: {e}")

    if total_new:
        logger.info(f"💰 [Sales Sync Worker] Стор {store_id}: сохранено {total_new} новых заказов.")

    # Скан кампании «Отзыв» (Фаза 5.1) — на каждом тике, не только при новых
    # заказах: настраиваемое окно "от"/"до" per-store может поймать заказ,
    # вставленный несколько тиков назад, только сегодня. Идемпотентно —
    # enqueue_outbound дедупит по БД.
    await asyncio.to_thread(scan_and_enqueue_review_campaign, store_id)
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
