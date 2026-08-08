"""
Кампания «Самовывоз» (Фаза 4) — детерминированное (не через ИИ) сообщение
клиенту с графиком/адресом сразу при оформлении заказа с самовывозом.
Триггер — backend/services/stock_sync_worker.py::_run_stock_radar_pass (уже
опрашивает NEW/APPROVED_BY_BANK заказы каждые 20с per-store, см. HANDOFF).
Постановка в очередь — backend/chatbot/outbound.py::enqueue_outbound, сам
рендер текста — backend/chatbot/prompt.py::render_pickup_campaign_message.
"""
import logging
import datetime as _dt

from backend.chatbot.prompt import get_profile, render_pickup_campaign_message
from backend.chatbot.outbound import enqueue_outbound
from backend.kaspi_wrapper import KaspiAPI

logger = logging.getLogger(__name__)

# Заказ с самовывозом может оформиться ночью — не пишем клиенту раньше 9 и
# позже 21 по Алматы, как и кампания «Отзыв» (см. review_campaign.py).
PICKUP_WINDOW_START_HOUR = 9
PICKUP_WINDOW_END_HOUR = 21


def _resolve_matched_point(token: str, pickup_points: list, pickup_point_id: str) -> dict | None:
    """Если у магазина настроена ровно одна точка — угадывать нечего, это она.
    Если несколько — пробуем сопоставить pickupPointId заказа с адресом из
    Kaspi pointofservices() и найти, какая из НАСТРОЕННЫХ владельцем точек
    похожа на него (подстрокой). Не находим — вернём None, вызывающий
    покажет клиенту все точки разом (см. render_pickup_campaign_message)."""
    if len(pickup_points) == 1:
        return pickup_points[0]
    if not pickup_point_id:
        return None

    try:
        pos_list = KaspiAPI.get_point_of_services(token)
    except Exception as e:
        logger.warning(f"[PickupCampaign] Не удалось получить pointofservices для сопоставления точки: {e}")
        return None

    kaspi_point = next((p for p in pos_list if p.get("id") == pickup_point_id), None)
    if not kaspi_point:
        return None

    kaspi_attr = kaspi_point.get("attributes", {})
    kaspi_addr = str(
        kaspi_attr.get("address", {}).get("formattedAddress")
        or kaspi_attr.get("name")
        or ""
    ).lower()
    if not kaspi_addr:
        return None

    for point in pickup_points:
        configured_addr = str(point.get("address", "")).lower()
        if configured_addr and (configured_addr in kaspi_addr or kaspi_addr in configured_addr):
            return point
    return None


async def maybe_trigger_pickup_campaign(store_id: str, token: str, order: dict):
    """Вызывается из _run_stock_radar_pass для каждого нового (ещё не
    обработанного) заказа. Тихо no-op, если: точек самовывоза не настроено,
    или кампания для этого заказа уже стояла в очереди (дедуп на уровне БД
    в enqueue_outbound).

    НЕ проверяет тут "чат-бот выключен у магазина" (раньше проверял) —
    выключенный бот тихо "съедал" заказы: _run_stock_radar_pass всё равно
    помечает заказ обработанным (ProcessedOrderDB), так что при выключенном
    боте кампания не ставилась в очередь и заказ терялся НАВСЕГДА — включение
    бота позже уже не подхватывало пропущенное. Теперь запись всегда уходит
    в очередь по факту заказа; "enabled" проверяется один раз, в момент
    реальной отправки — outbound.py::run_outbound_cycle. Так включение бота
    в любой момент подхватывает всё, что успело накопиться (и не сгорело по
    Гейту 5 — см. outbound.py), а не только заказы после включения."""
    import asyncio

    try:
        profile = await asyncio.to_thread(get_profile, store_id)
        pickup_points = profile.get("pickup_points") or []
        if not pickup_points:
            return

        pickup_point_id = order.get("pickupPointId")
        matched_point = await asyncio.to_thread(_resolve_matched_point, token, pickup_points, pickup_point_id)

        message_text = render_pickup_campaign_message(profile, matched_point)
        if not message_text:
            return

        order_id = str(order.get("id") or order.get("internalId") or "")
        queued = await asyncio.to_thread(
            enqueue_outbound,
            store_id, order_id, "pickup", message_text, _dt.datetime.utcnow(),
            PICKUP_WINDOW_START_HOUR, PICKUP_WINDOW_END_HOUR,
        )
        if queued:
            logger.info(f"📤 [PickupCampaign] Заказ {order_id} (стор {store_id}): сообщение о самовывозе поставлено в очередь.")
    except Exception as e:
        logger.error(f"[PickupCampaign] Сбой триггера для заказа {order.get('id')}: {e}")
