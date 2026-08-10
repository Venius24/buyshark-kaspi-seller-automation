"""
Кампания «Отзыв» (Фаза 5, Фаза 5.1 — переход на скан по дням 2026-08-11) —
детерминированное (не через ИИ) сообщение с просьбой оставить отзыв, через
настраиваемое per-store окно дней после того, как заказ впервые замечен
завершённым (state=ARCHIVE, status=COMPLETED).

ЗАЧЕМ так (бизнес-логика, 2026-08-09/2026-08-11, зафиксировано со слов
пользователя — не переизобретать, не переоткрывать без явного запроса):
Человек оформляет заказ → через 3-4 дня забирает его, заказ уходит в архив
(COMPLETED) → ещё через несколько дней человек уже успел воспользоваться
товаром — вот тогда пишем "не хотели бы оставить отзыв?".

Кому НЕ пишем: тем, кто уже писал нам в чат по этому заказу (гейт в
outbound.py, коммит 339278b) — почти всегда это размер/отказ/возврат, не
кандидат на отзыв.

Фаза 5.1 (2026-08-11): раньше сообщение планировалось РАЗ, сразу в момент
обнаружения заказа (`scheduled_at = now + REVIEW_DELAY_DAYS`, фиксированные
4 дня). Пользователь попросил вместо фиксированной задержки — настраиваемое
per-store окно "от"/"до" дней (см. backend/chatbot/prompt.py::DEFAULT_PROFILE
review_delay_from_days/review_delay_to_days/review_send_overdue), проверяемое
скan'ом, а не разовым планированием вперёд. Правило (со слов пользователя):
  - задано только "от" (N), без "до" и без чекбокса — шлём РОВНО тем, у кого
    days_since_completion == N (не "от N и старше" — это была бы другая
    семантика, пользователь явно попросил "только равно").
  - задано "от" и "до" — диапазон [от, до] включительно.
  - чекбокс review_send_overdue включён (и "до" не задано) — ослабляет
    условие до days_since_completion >= "от", ловит пропущенных/просроченных.
    Дедуп берёт на себя enqueue_outbound (UniqueConstraint по БД) — заказ,
    уже поставленный в очередь раньше, просто не попадёт повторно.
Скан вызывается на каждом тике backend/services/sales_sync_worker.py (не
отдельным суточным циклом — тик и так раз в 30 мин, дешевле не заводить ещё
один фоновый луп; сам скан идемпотентен благодаря дедупу выше, так что более
частый вызов не создаёт дублей, а только увеличивает точность "в какой день
поймали").

Kaspi Order API не отдаёт точную дату фактической доставки курьером — самый
надёжный доступный сигнал "заказ доставлен" это переход в COMPLETED, который
видит backend/services/sales_sync_worker.py (тик каждые 30 мин), а точка
отсчёта "дней с доставки" — SalesOrderDB.ingested_at (момент INSERT, не
трогается при повторных апдейтах той же строки, см. state_helpers.py::
_upsert_sales_orders) — та же аппроксимация, что была в исходной реализации.

Холодный 90-дневный бэкафилл нового магазина исключён из скана колонкой
SalesOrderDB.review_eligible (см. миграцию 025) — проставляется False при
вставке заказов бэкафилла (backend/services/sales_sync_worker.py), иначе
скан поймал бы всю полугодовую историю разом при первом же подключении стора.

Постановка в очередь — backend/chatbot/outbound.py::enqueue_outbound (та же
инфраструктура, что и у кампании «Самовывоз», Фаза 4). Флаг awaiting_review_reply,
подмешиваемый в следующий промпт ИИ на ответ клиента — backend/chatbot/config.py
+ backend/chatbot/prompt.py, ставится в outbound.py при реальной отправке.

Прямая ссылка на форму отзыва (2026-08-09, см. [[project_kaspi_review_deeplink]]
в памяти агента): Kaspi отдаёт productCode товара прямо в orders API
(relationships.product.data.id, base64 — декодируется тем же
KaspiAPI._decode_base64_if_needed, что и Master SKU радара), так что
backend/kaspi_wrapper.py::get_completed_orders прокидывает его в каждый entry,
и он же сохраняется в SalesOrderDB.entries_payload как есть. render_review_message()
собирает orderCode+productCode в `kaspi.kz/shop/review/productreview?...&rating=5`
— вручную подтверждено на живом заказе, что это НЕ авто-постинг: ссылка открывает
форму с предзаполненной оценкой, клиент сам пишет комментарий и жмёт отправить.
Если productCode не пришёл (например, entries пустые) — деградация на обычный
текст без ссылки, кампания не срывается.
"""
import json
import logging
import datetime as _dt

from backend.chatbot.outbound import enqueue_outbound
from backend.chatbot import prompt as bot_prompt

logger = logging.getLogger(__name__)

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


def _should_send(days_since: int, from_days: int, to_days, send_overdue: bool) -> bool:
    """Правило окна (см. докстринг модуля, зафиксировано со слов пользователя
    2026-08-11): "до" задано — диапазон [от, до]; "до" не задано и включён
    чекбокс "слать пропущенным" — от N дней и старше; иначе — РОВНО N дней,
    не раньше и не позже."""
    if to_days is not None:
        return from_days <= days_since <= to_days
    if send_overdue:
        return days_since >= from_days
    return days_since == from_days


def scan_and_enqueue_review_campaign(store_id: str) -> int:
    """Скан завершённых заказов магазина (вызывается на каждом тике
    sales_sync_worker) — для каждого review_eligible заказа считает
    days_since = сегодня - ingested_at и ставит в очередь тех, кто попал в
    настроенное окно (backend/chatbot/prompt.py::DEFAULT_PROFILE). Идемпотентно:
    enqueue_outbound сам отсеивает уже поставленные в очередь заказы (дедуп по
    БД), так что безопасно вызывать чаще, чем раз в день. Возвращает число
    реально поставленных в очередь записей."""
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import SalesOrderDB
    from sqlalchemy import select

    profile = bot_prompt.get_profile(store_id)
    from_days = int(profile.get("review_delay_from_days") or 4)
    to_days_raw = profile.get("review_delay_to_days")
    to_days = int(to_days_raw) if to_days_raw not in (None, "") else None
    send_overdue = bool(profile.get("review_send_overdue"))

    session = _get_sync_db_session()
    if not session:
        return 0
    try:
        now = _dt.datetime.utcnow()
        rows = session.execute(
            select(SalesOrderDB).where(
                SalesOrderDB.store_id == store_id,
                SalesOrderDB.review_eligible.is_(True),
                SalesOrderDB.status == "COMPLETED",
                SalesOrderDB.state == "ARCHIVE",
            )
        ).scalars().all()
        candidates = [
            row for row in rows
            if _should_send((now - row.ingested_at).days, from_days, to_days, send_overdue)
        ]
        session.expunge_all()
    except Exception as e:
        logger.error(f"[ReviewCampaign] Сбой скана для стора {store_id}: {e}")
        return 0
    finally:
        session.close()

    queued_count = 0
    for row in candidates:
        try:
            entries = json.loads(row.entries_payload or "[]")
        except Exception:
            entries = []
        order_id = row.order_code or row.order_internal_id
        message_text = render_review_message(order_id, {"entries": entries})
        try:
            queued = enqueue_outbound(
                store_id, order_id, "review", message_text, now,
                REVIEW_WINDOW_START_HOUR, REVIEW_WINDOW_END_HOUR,
            )
        except Exception as e:
            logger.error(f"[ReviewCampaign] Сбой постановки в очередь заказа {order_id} (стор {store_id}): {e}")
            continue
        if queued:
            queued_count += 1

    if queued_count:
        logger.info(f"📤 [ReviewCampaign] Стор {store_id}: {queued_count} запрос(ов) на отзыв поставлено в очередь скан-ом.")
    return queued_count
