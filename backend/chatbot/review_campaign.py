"""
Кампания «Отзыв» (Фаза 5) — детерминированное (не через ИИ) сообщение с
просьбой оставить отзыв, через 4 дня после того, как заказ впервые замечен
завершённым (state=ARCHIVE, status=COMPLETED).

ЗАЧЕМ так (бизнес-логика, 2026-08-09, зафиксировано со слов пользователя —
не переизобретать, не переоткрывать без явного запроса):
Человек оформляет заказ → через 3-4 дня забирает его, заказ уходит в архив
(COMPLETED) → ещё через 3-4 дня (REVIEW_DELAY_DAYS) человек уже успел
воспользоваться товаром — вот тогда пишем "не хотели бы оставить отзыв?".

Кому НЕ пишем: тем, кто уже писал нам в чат по этому заказу (гейт в
outbound.py, коммит 339278b). Причина — если клиент написал, это почти
всегда узнать размер / отказаться от покупки / оформить возврат (пришло не
то или сломанное). Это не кандидат на отзыв, ему просить отзыв не нужно и
неуместно.

ВАЖНОЕ СЛЕДСТВИЕ (из-за чего 2026-08-09 была путаница): целевая аудитория
кампании по определению — заказы БЕЗ существующего чата. Но текущая
реализация (resolve_group_id → send_message) умеет писать только ВНУТРЬ уже
существующего чата и не умеет открывать новый (Playwright "Фаза 4
write-first" не реализована). То есть ровно та аудитория, для которой
кампания задумана, технически недостижима, пока Фаза 4 не построена — это
не баг гейта из 339278b и не изобретённое агентом ограничение, а реальный
пробел в автоматизации, который нужно закрывать отдельно.

Kaspi Order API не отдаёт точную дату фактической доставки курьером — самый
надёжный доступный сигнал "заказ доставлен" это переход в COMPLETED, который
видит backend/services/sales_sync_worker.py (тик каждые 30 мин). Поэтому
"через 4 дня после доставки" реализовано как "через 4 дня после того как
воркер впервые увидел заказ COMPLETED" — расхождение максимум ~30 мин (частота
опроса), несущественно на фоне целого дня в окне отправки.

Постановка в очередь — backend/chatbot/outbound.py::enqueue_outbound (та же
инфраструктура, что и у кампании «Самовывоз», Фаза 4). Флаг awaiting_review_reply,
подмешиваемый в следующий промпт ИИ на ответ клиента — backend/chatbot/config.py
+ backend/chatbot/prompt.py, ставится в outbound.py при реальной отправке.

Прямая ссылка на форму отзыва (2026-08-09, см. [[project_kaspi_review_deeplink]]
в памяти агента): Kaspi отдаёт productCode товара прямо в orders API
(relationships.product.data.id, base64 — декодируется тем же
KaspiAPI._decode_base64_if_needed, что и Master SKU радара), так что
backend/kaspi_wrapper.py::get_completed_orders прокидывает его в каждый entry.
render_review_message() собирает orderCode+productCode в
`kaspi.kz/shop/review/productreview?...&rating=5` — вручную подтверждено на
живом заказе, что это НЕ авто-постинг: ссылка открывает форму с предзаполненной
оценкой, клиент сам пишет комментарий и жмёт отправить. Если productCode не
пришёл (например, entries пустые) — деградация на обычный текст без ссылки,
кампания не срывается.
"""
import logging
import datetime as _dt

from backend.chatbot.outbound import enqueue_outbound

logger = logging.getLogger(__name__)

REVIEW_DELAY_DAYS = 4
REVIEW_WINDOW_START_HOUR = 11
REVIEW_WINDOW_END_HOUR = 19

REVIEW_MESSAGE_TEMPLATE = (
    "Здравствуйте! Мы очень рады, что заказ доехал до вас 🙌\n"
    "Если товар понравился — будем благодарны за отзыв на Kaspi, это правда "
    "помогает нам расти. А если что-то не так — просто напишите нам прямо "
    "здесь, разберёмся!"
)

# Прямая ссылка на форму отзыва Kaspi с открытой (не самоотправляющейся —
# подтверждено вручную 2026-08-09, см. [[project_kaspi_review_deeplink]])
# 5-звёздочной оценкой; комментарий клиент пишет и подтверждает сам.
REVIEW_DEEPLINK_TEMPLATE = "https://kaspi.kz/shop/review/productreview?orderCode={order_code}&productCode={product_code}&rating=5"


def _first_product_code(order: dict) -> str:
    for entry in order.get("entries") or []:
        code = entry.get("productCode")
        if code:
            return str(code)
    return ""


def render_review_message(order_id: str, order: dict) -> str:
    """Текст кампании «Отзыв» — базовый шаблон + прямая ссылка на форму
    отзыва (с предзаполненной оценкой 5), если Kaspi отдал productCode для
    первого товара заказа. Без productCode шлём просто текст без ссылки —
    деградация, а не срыв кампании."""
    product_code = _first_product_code(order)
    if not product_code:
        return REVIEW_MESSAGE_TEMPLATE
    link = REVIEW_DEEPLINK_TEMPLATE.format(order_code=order_id, product_code=product_code)
    return f"{REVIEW_MESSAGE_TEMPLATE}\n\n⭐ Оставить отзыв в 1 клик: {link}"


async def maybe_trigger_review_campaign(store_id: str, order: dict):
    """Вызывается из sales_sync_worker для каждого НОВОГО (впервые увиденного
    в этом тике) завершённого заказа. Дедуп самой кампании на уровне БД в
    enqueue_outbound.

    НЕ проверяет тут "чат-бот выключен у магазина" (раньше проверял) — при
    выключенном боте sales_sync_worker всё равно апсертит заказ в
    sales_orders, так что запись становится "не новой" навсегда — включение
    бота позже уже не подхватывало пропущенные отзывы. Теперь запись всегда
    ставится в очередь по факту завершённого заказа; "enabled" проверяется
    один раз, в момент реальной отправки — outbound.py::run_outbound_cycle.

    ВАЖНО: вызывающий код (sales_sync_worker._sync_store_sales) обязан НЕ
    вызывать это на холодном 90-дневном бэкафилле нового стора — иначе все
    старые завершённые заказы разом получат запрос на отзыв "через 4 дня от
    сейчас", что и поздно, и похоже на спам."""
    import asyncio

    try:
        order_id = str(order.get("code") or order.get("internalId") or "")
        if not order_id:
            return

        message_text = render_review_message(order_id, order)

        scheduled_at = _dt.datetime.utcnow() + _dt.timedelta(days=REVIEW_DELAY_DAYS)
        queued = await asyncio.to_thread(
            enqueue_outbound,
            store_id, order_id, "review", message_text, scheduled_at,
            REVIEW_WINDOW_START_HOUR, REVIEW_WINDOW_END_HOUR,
        )
        if queued:
            logger.info(f"📤 [ReviewCampaign] Заказ {order_id} (стор {store_id}): запрос на отзыв поставлен в очередь на {scheduled_at.date()}.")
    except Exception as e:
        logger.error(f"[ReviewCampaign] Сбой триггера для заказа {order.get('code')}: {e}")
