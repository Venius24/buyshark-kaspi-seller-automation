"""
Каркас исходящих рассылок (Фаза 3) — общая очередь+воркер под детерминированные
кампании «Самовывоз» (Фаза 4, backend/chatbot/pickup_campaign.py) и «Отзыв»
(Фаза 5, backend/chatbot/review_campaign.py). Сами кампании (когда и что
поставить в очередь) живут в тех модулях — этот модуль только:
  - enqueue_outbound() — поставить сообщение в очередь (дедуп по БД);
  - resolve_group_id() — найти group_id заказа (кэш -> HTTP-скан getDiffGroups);
  - run_outbound_cycle() — один тик воркера: гейты + отправка due-сообщений;
  - outbound_loop() — фоновый цикл, аналог kaspi_chatbot_loop.

Гейты (все — до отправки любого сообщения):
  0. Бот выключен у стора (chatbot enabled=False) — run_outbound_cycle()
     целиком пропускает стор в этом тике. НЕ помечает записи никак (не
     'skipped', остаются 'pending') — в отличие от гейтов 2-5 ниже, это не
     решение "не отправлять эту запись", а просто пауза всей отправки.
     Постановка в очередь (campaigns/*.py) от этого флага НЕ зависит —
     иначе заказы, пришедшие пока бот выключен, терялись бы навсегда (см.
     историю правки 2026-08-09 в HANDOFF/памяти агента).
  1. Дедуп — на уровне БД (UniqueConstraint store_id+order_id+campaign в
     OutboundQueueDB), enqueue_outbound() просто ловит конфликт.
  2. Тихие часы — window_start_hour/window_end_hour на самой записи (в
     часовом поясе Asia/Almaty), если заданы. Если сейчас вне окна — запись
     остаётся pending, воркер просто её пропускает в этом тике.
  3. human_override — если group_id уже известен и чат заблокирован (клиент
     попросил менеджера) — исходящее не отправляется, статус 'skipped'.
  4. Дневной лимит на магазин — анти-спам/anti-ban, MAX_OUTBOUND_PER_DAY.
  5. Устаревание — STALE_AFTER_DAYS. Если запись просрочена (due) больше чем
     на этот срок, она молча уходит в status='skipped' вместо отправки.

**ЗАДЕКЛАРИРОВАНО (2026-08-08, тестовый лимит; поднято 2026-08-13 по
решению пользователя с 30 до 75, 2026-10-09 — до 150):**
MAX_OUTBOUND_PER_DAY=150 (текст ниже написан при 75) — по-прежнему НЕ отмасштабировано под реальный объём
Barkhan (счёт на сотни завершённых заказов в день — обнаружено на живых данных:
один тик sales_sync_worker поставил в очередь 225 сообщений кампании «Отзыв»
за раз), просто выше первоначальной тестовой отметки 30.
Если оставить очередь копиться без ограничения срока, "отзыв через 4 дня
после доставки" при таком разрыве между притоком и лимитом реально уходил бы
клиенту через 2-3 НЕДЕЛИ — бессмысленно по сути кампании. По прямому решению
пользователя ГЕЙТ 5 (STALE_AFTER_DAYS, см. _process_store_queue) решает это
не поднятием лимита, а срезкой очереди: то, что не успело уйти в течение
разумного окна после планового времени, просто пропускается (`skip_reason`
объясняет, что именно). Значит при текущем лимите БОЛЬШАЯ ЧАСТЬ
кампании «Отзыв» у активного магазина будет уходить в 'skipped', а не
отправляться с большим опозданием — это осознанный компромисс на время
тестирования, а не баг. Подъём MAX_OUTBOUND_PER_DAY под реальный объём —
отдельная будущая задача, не Фазы 6.

ПЕРВЫЙ КОНТАКТ (Фаза 4, добито 2026-08-09): если group_id ещё не известен
(по заказу никогда не было переписки), resolve_group_id() не находит его
HTTP-сканом (группа физически не существует, пока никто не написал первым)
— в этом случае _process_store_queue сама вызывает
backend.services.playwright_first_contact.send_first_contact() (живой,
дважды вручную подтверждённый Playwright-сценарий: открыть заказ ->
«Сообщения по заказу» -> написать). Это отдельный, более медленный и
хрупкий путь (реальный запуск браузера на заказ), поэтому у него СВОЙ
лимит на тик — FIRST_CONTACT_MAX_PER_TICK, не смешанный с
MAX_OUTBOUND_PER_DAY (тот всё равно тоже растёт при успехе — это такая же
реальная исходящая отправка). Если Playwright-сценарий не смог отправить
(чаще всего — протухла SMS-чат-сессия, живёт ~5 часов и продлевается
только вручную), запись остаётся в 'needs_first_contact' с причиной в
skip_reason и будет ретраиться на следующих тиках, пока не устареет
(Гейт 5).
"""
import logging
import asyncio
import random
import datetime as _dt
from typing import Optional

from sqlalchemy import select, func

from backend.chatbot import config
from backend.chatbot import chat_client
from backend.chatbot.kaspi import load_chat_cookies
from backend.dumper.config import discover_merchant_id
from backend.security.store_context import list_active_stores
from backend.state_helpers import _get_sync_db_session
from backend.services.playwright_first_contact import send_first_contact, STATUS_BY_CAMPAIGN as FIRST_CONTACT_STATUS_BY_CAMPAIGN

logger = logging.getLogger(__name__)

# Казахстан не переходит на летнее время — фиксированный UTC+5 всегда
# корректен, без зависимости от пакета tzdata (не установлен в этом venv).
ALMATY_TZ = _dt.timezone(_dt.timedelta(hours=5))
MAX_OUTBOUND_PER_DAY = 150
# Первый контакт — реальный запуск браузера на заказ (~10-20с, медленнее и
# хрупче HTTP-отправки в уже существующий чат). Лимит НА ТИК (не на день,
# как MAX_OUTBOUND_PER_DAY) — осознанный выбор без доп. состояния в БД:
# при тике раз в 60с и 3 заказах по ~15с воркер всё ещё укладывается в
# межтиковый интервал. Поднимать вместе с ростом уверенности в стабильности
# сценария, не заранее.
FIRST_CONTACT_MAX_PER_TICK = 3
# После стольких неудачных первых контактов запись уходит в 'failed'. Иначе
# она до срезки по Гейту 5 (3 дня) занимает голову выборки _load_due_pending
# и рассылка встаёт: так 02.10.2026 после 11:22 не ушло ни одного сообщения.
FIRST_CONTACT_MAX_ATTEMPTS = 3
# Гейт 5 (см. докстринг выше) — просроченные больше этого срока записи
# срезаются в 'skipped' вместо отправки, чтобы очередь не растягивалась
# на недели при MAX_OUTBOUND_PER_DAY меньше реального дневного притока.
STALE_AFTER_DAYS = 3


def enqueue_outbound(
    store_id: str,
    order_id: str,
    campaign: str,
    message_text: str,
    scheduled_at: _dt.datetime,
    window_start_hour: Optional[int] = None,
    window_end_hour: Optional[int] = None,
    group_id: Optional[str] = None,
) -> bool:
    """Ставит сообщение в очередь. Возвращает False, если эта кампания для
    этого заказа уже была поставлена в очередь ранее (дедуп по БД) — не
    исключение, а штатный путь (заказ мог повторно попасть в триггер)."""
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import OutboundQueueDB
        existing = session.execute(
            select(OutboundQueueDB).where(
                OutboundQueueDB.store_id == store_id,
                OutboundQueueDB.order_id == order_id,
                OutboundQueueDB.campaign == campaign,
            )
        ).scalar_one_or_none()
        if existing:
            return False

        session.add(OutboundQueueDB(
            store_id=store_id,
            order_id=order_id,
            campaign=campaign,
            group_id=group_id,
            message_text=message_text,
            status="pending",
            scheduled_at=scheduled_at,
            window_start_hour=window_start_hour,
            window_end_hour=window_end_hour,
            created_at=_dt.datetime.utcnow(),
        ))
        session.commit()
        return True
    except Exception as e:
        logger.error(f"[Outbound] Ошибка постановки в очередь ({campaign}/{order_id}): {e}")
        session.rollback()
        return False
    finally:
        session.close()


def _load_cached_group_id(store_id: str, order_id: str) -> Optional[str]:
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import OrderChatLinkDB
        link = session.get(OrderChatLinkDB, (store_id, order_id))
        return link.group_id if link else None
    finally:
        session.close()


# Скан order_id -> group_id: getDiffGroups отдаёт диалоги от старых к новым,
# поэтому прежний запрос «30 дней назад, limit 200» видел только диалоги
# месячной давности, и свежий заказ с уже идущей перепиской считался «без чата»
# (уходил в Playwright первым контактом, мимо правила «не слать в живой диалог»).
# Теперь листаем вперёд и запоминаем курсор на стор: после первого прохода
# каждый тик стоит один запрос.
LINK_SCAN_DAYS = 30
LINK_SCAN_MAX_PAGES = 25
_link_scan_cursor: dict = {}


def _cache_order_chat_links(store_id: str, pairs: dict):
    if not pairs:
        return
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import OrderChatLinkDB
        now = _dt.datetime.utcnow()
        for order_id, group_id in pairs.items():
            existing = session.get(OrderChatLinkDB, (store_id, order_id))
            if existing:
                existing.group_id = group_id
                existing.resolved_at = now
            else:
                session.add(OrderChatLinkDB(store_id=store_id, order_id=order_id, group_id=group_id, resolved_at=now))
        session.commit()
    except Exception as e:
        logger.error(f"[Outbound] Ошибка кэширования order_chat_links ({len(pairs)} шт.): {e}")
        session.rollback()
    finally:
        session.close()


async def sync_order_chat_links(store_id: str, cookies: dict, merchant_id: str):
    """Дочитывает диалоги, изменённые с прошлого скана, и кэширует их order_id."""
    gts = _link_scan_cursor.get(store_id) or int(_dt.datetime.utcnow().timestamp() * 1000) - LINK_SCAN_DAYS * 24 * 3600 * 1000
    for _ in range(LINK_SCAN_MAX_PAGES):
        data = await chat_client.diff_page(cookies, merchant_id, gts, limit=200)
        if data is None:
            return
        groups = data.get("groups") or []
        pairs = {}
        for g in groups:
            order_id = (g.get("analytics") or {}).get("order_id")
            if order_id and g.get("id"):
                pairs[str(order_id)] = g["id"]
        await asyncio.to_thread(_cache_order_chat_links, store_id, pairs)
        if not groups:
            if data.get("sts"):
                _link_scan_cursor[store_id] = max(gts, data["sts"])
            return
        page_max = max((g.get("gts") or 0 for g in groups), default=0)
        if page_max <= gts:
            return
        gts = page_max
        _link_scan_cursor[store_id] = gts
        if not data.get("more"):
            return


async def resolve_group_id(store_id: str, order_id: str, cookies: dict, merchant_id: str) -> Optional[str]:
    """Кэш order_chat_links (+ дочитка свежих диалогов). Не запускает
    Playwright — если группы физически ещё нет (заказ без переписки),
    возвращает None."""
    cached = await asyncio.to_thread(_load_cached_group_id, store_id, order_id)
    if cached:
        return cached
    await sync_order_chat_links(store_id, cookies, merchant_id)
    return await asyncio.to_thread(_load_cached_group_id, store_id, order_id)


def _is_within_window(item) -> bool:
    if item.window_start_hour is None or item.window_end_hour is None:
        return True
    now_hour = _dt.datetime.now(ALMATY_TZ).hour
    return item.window_start_hour <= now_hour < item.window_end_hour


def _sent_today_count(store_id: str) -> int:
    session = _get_sync_db_session()
    if not session:
        return 0
    try:
        from backend.db.models import OutboundQueueDB
        # Полночь по Алматы (не UTC) — иначе дневная квота фактически
        # сбрасывалась бы в 5 утра по местному времени, а не в начале суток
        # (см. ALMATY_TZ выше, тот же оффсет что и у окна тихих часов).
        almaty_now = _dt.datetime.now(ALMATY_TZ)
        almaty_midnight = almaty_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start = almaty_midnight.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return session.execute(
            select(func.count()).select_from(OutboundQueueDB).where(
                OutboundQueueDB.store_id == store_id,
                OutboundQueueDB.status == "sent",
                OutboundQueueDB.sent_at >= today_start,
            )
        ).scalar_one()
    finally:
        session.close()


def _load_due_pending(store_id: str, limit: int = 20) -> list:
    """Возвращает due-записи, отсортированные так, чтобы «Самовывоз» ВСЕГДА
    попадал в выборку этого тика первым — а не только "вне дневного лимита"
    в _process_store_queue. Иначе при большом объёмном бэклоге «Отзыва»
    (может быть 1000+ pending с более ранним scheduled_at, чем у любого
    самовывоза) записи pickup физически не попадали бы даже в этот LIMIT-срез
    по scheduled_at ASC — гейт по лимиту до них просто не доходил бы."""
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from sqlalchemy import case
        from backend.db.models import OutboundQueueDB
        now = _dt.datetime.utcnow()
        rows = session.execute(
            select(OutboundQueueDB).where(
                OutboundQueueDB.store_id == store_id,
                OutboundQueueDB.status.in_(("pending", "needs_first_contact")),
                OutboundQueueDB.scheduled_at <= now,
            ).order_by(
                case((OutboundQueueDB.campaign == "pickup", 0), else_=1),
                OutboundQueueDB.scheduled_at,
            ).limit(limit)
        ).scalars().all()
        session.expunge_all()
        return rows
    finally:
        session.close()


def mark_replied_if_pending(store_id: str, group_id: str, at: Optional[_dt.datetime] = None):
    """Фаза 6: помечает САМОЕ СВЕЖЕЕ отправленное исходящее в этом чате как
    "клиент ответил" — вызывается из worker.py на КАЖДОЕ входящее сообщение
    клиента, до ветвления на human_triggers/логистику/ИИ (в отличие от
    chat_response_logs, который не пишется на ветке HUMAN_OVERRIDE и поэтому
    занижал бы долю ответивших именно на самых важных, негативных ответах).
    Идемпотентно — если уже отмечено, повторные вызовы no-op."""
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import OutboundQueueDB
        row = session.execute(
            select(OutboundQueueDB).where(
                OutboundQueueDB.store_id == store_id,
                OutboundQueueDB.group_id == group_id,
                OutboundQueueDB.status == "sent",
                OutboundQueueDB.replied_at.is_(None),
            ).order_by(OutboundQueueDB.sent_at.desc()).limit(1)
        ).scalar_one_or_none()
        if not row:
            return
        row.replied_at = at or _dt.datetime.utcnow()
        session.commit()
    except Exception as e:
        logger.error(f"[Outbound] Ошибка отметки ответа клиента ({group_id}): {e}")
        session.rollback()
    finally:
        session.close()


def _mark(item_id: int, status: str, skip_reason: Optional[str] = None, group_id: Optional[str] = None):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import OutboundQueueDB
        row = session.get(OutboundQueueDB, item_id)
        if not row:
            return
        row.status = status
        if skip_reason is not None:
            # skip_reason — varchar(200): тексты ошибок Playwright легко длиннее
            # (Page.goto timeout + Call log). Без среза UPDATE падает с
            # "value too long", и статус записи вообще не обновляется.
            row.skip_reason = skip_reason[:200]
        if group_id is not None:
            row.group_id = group_id
        if status == "sent":
            row.sent_at = _dt.datetime.utcnow()
        row.attempts += 1
        session.commit()
    except Exception as e:
        logger.error(f"[Outbound] Ошибка обновления статуса очереди #{item_id}: {e}")
        session.rollback()
    finally:
        session.close()


async def _process_store_queue(store_id: str, cookies: dict, merchant_id: str):
    due = await asyncio.to_thread(_load_due_pending, store_id)
    if not due:
        return

    sent_today = await asyncio.to_thread(_sent_today_count, store_id)
    first_contact_this_tick = 0
    now = _dt.datetime.utcnow()

    for item in due:
        # Гейт 5 (см. докстринг модуля): просрочка больше STALE_AFTER_DAYS —
        # не спамим клиенту "с опозданием на пару недель", просто пропускаем.
        # Не тратит дневной лимит — освобождает его для более свежих записей.
        if now - item.scheduled_at > _dt.timedelta(days=STALE_AFTER_DAYS):
            await asyncio.to_thread(
                _mark, item.id, "skipped",
                f"устарело — не отправлено в течение {STALE_AFTER_DAYS} дн. после планового времени (дневной лимит {MAX_OUTBOUND_PER_DAY}/день не успевал)",
            )
            continue

        # «Самовывоз» — вне общего дневного лимита (решение пользователя
        # 2026-08-21): это разовое time-sensitive сообщение с адресом/графиком
        # клиенту, который прямо сейчас стоит и ждёт, а не массовая рассылка
        # вроде «Отзыва» (до 225 записей за один тик sales_sync_worker). При
        # общем лимите на магазин «Отзыв» съедал квоту раньше по scheduled_at
        # и «Самовывоз» неделями стоял в очереди, пока не сгорал по Гейту 5
        # (STALE_AFTER_DAYS) — клиент так и не получал адрес точки выдачи.
        if item.campaign != "pickup" and sent_today >= MAX_OUTBOUND_PER_DAY:
            logger.info(f"[Outbound] Стор {store_id}: дневной лимит {MAX_OUTBOUND_PER_DAY} исходящих достигнут, откладываю остальное.")
            continue

        if not _is_within_window(item):
            continue

        group_id = item.group_id
        if not group_id:
            group_id = await resolve_group_id(store_id, item.order_id, cookies, merchant_id)
            if not group_id:
                status_filter = FIRST_CONTACT_STATUS_BY_CAMPAIGN.get(item.campaign)
                if not status_filter:
                    await asyncio.to_thread(_mark, item.id, "needs_first_contact", f"первый контакт не поддержан для кампании {item.campaign!r}")
                    continue
                if first_contact_this_tick >= FIRST_CONTACT_MAX_PER_TICK:
                    continue  # остаётся needs_first_contact, попробуем на следующем тике
                first_contact_this_tick += 1

                ok, detail = await asyncio.to_thread(send_first_contact, cookies, item.order_id, item.message_text, status_filter)
                if not ok:
                    give_up = (item.attempts or 0) + 1 >= FIRST_CONTACT_MAX_ATTEMPTS
                    await asyncio.to_thread(
                        _mark, item.id, "failed" if give_up else "needs_first_contact",
                        f"первый контакт не удался: {detail}",
                    )
                    logger.warning(
                        f"[Outbound] Первый контакт по заказу {item.order_id} не удался"
                        f"{' — сдаюсь' if give_up else ''} (попытка {(item.attempts or 0) + 1}): {detail}"
                    )
                    await asyncio.sleep(random.uniform(2.5, 6.0))
                    continue

                # send_first_contact уже отправил сообщение сам (Playwright
                # fill+Enter внутри виджета) — это не тот же путь, что ниже
                # (chat_client.send_message в уже существующий чат), повторно
                # слать через HTTP не нужно. group_id пробуем подтянуть сразу
                # же для awaiting_review_reply/будущих ответов клиента —
                # best-effort, не блокируем на этом успешную отправку.
                resolved_group_id = await resolve_group_id(store_id, item.order_id, cookies, merchant_id)
                await asyncio.to_thread(_mark, item.id, "sent", group_id=resolved_group_id)
                sent_today += 1
                logger.info(f"✅ [Outbound] {item.campaign} отправлено ПЕРВЫМ КОНТАКТОМ (Playwright) по заказу {item.order_id} (стор {store_id}).")
                if item.campaign == "review" and resolved_group_id:
                    await asyncio.to_thread(config.set_awaiting_review_reply, store_id, resolved_group_id)
                await asyncio.sleep(random.uniform(2.5, 6.0))
                continue

        if config.is_chat_human_override(store_id, group_id):
            await asyncio.to_thread(_mark, item.id, "skipped", "human_override", group_id)
            continue

        # Кампания «Отзыв» — не лезем с шаблонным автозапросом, если по
        # заказу уже идёт живая переписка с клиентом (решение пользователя
        # 2026-08-09): шаблон "оставьте отзыв" рядом с реальным диалогом
        # выглядит роботизированно и может перебить более важный разговор
        # (жалоба, вопрос по заказу). Кампании «Самовывоз» это не касается —
        # она сама пишет первой и такой переписки по определению ещё нет.
        if item.campaign == "review":
            history = await chat_client.get_history(group_id, cookies, merchant_id, limit=1)
            if history:
                await asyncio.to_thread(_mark, item.id, "skipped", "уже есть переписка с клиентом по заказу — не шлём автозапрос на отзыв", group_id)
                continue

        result = await chat_client.send_message(group_id, item.message_text, cookies, merchant_id)
        if result.get("success"):
            await asyncio.to_thread(_mark, item.id, "sent", group_id=group_id)
            sent_today += 1
            logger.info(f"✅ [Outbound] {item.campaign} отправлено по заказу {item.order_id} (стор {store_id}).")
            if item.campaign == "review":
                # Фаза 5: одноразовый флаг — снимается на первом же ответе
                # клиента в _run_chatbot_pass (backend/chatbot/worker.py).
                await asyncio.to_thread(config.set_awaiting_review_reply, store_id, group_id)
        else:
            await asyncio.to_thread(_mark, item.id, "failed", str(result.get("error")), group_id)
            logger.error(f"❌ [Outbound] Сбой отправки {item.campaign} по заказу {item.order_id}: {result.get('error')}")

        # Пауза между реальными отправками разным клиентам (не после
        # staleness/лимита/окна/human_override — там Kaspi не трогали) —
        # без неё несколько due-записей на одном тике улетали бы клиентам
        # практически одновременно, что для анти-спам защиты Kaspi выглядит
        # как бот сильнее, чем естественная переписка человека.
        await asyncio.sleep(random.uniform(2.5, 6.0))


async def run_outbound_cycle():
    """Один тик по всем активным сторам — вызывается из outbound_loop().

    "enabled" стора проверяется ИМЕННО тут (не на моменте постановки в
    очередь campaigns/*.py) — единая точка паузы отправки. Пока бот выключен,
    due-записи просто копятся в очереди (см. pickup_campaign.py/
    review_campaign.py), а не теряются молча; включение бота в любой момент
    подхватывает всё накопленное на следующем же тике (что не успело
    устареть по Гейту 5 — см. _process_store_queue)."""
    stores = await list_active_stores()
    for store in stores:
        try:
            cfg = await asyncio.to_thread(config.load_chatbot_config, store.store_id)
            if not cfg.get("enabled", True):
                continue

            from backend.billing import enforcement as billing_enforcement
            blocked, _reason = await asyncio.to_thread(billing_enforcement.is_service_blocked, store.store_id)
            if blocked:
                continue

            cookies = await asyncio.to_thread(load_chat_cookies, store_id=store.store_id)
            if not cookies:
                continue
            merchant_id = await asyncio.to_thread(discover_merchant_id, store.token)
            if not merchant_id:
                continue
            await _process_store_queue(store.store_id, cookies, merchant_id)
        except Exception as e:
            logger.error(f"[Outbound] Стор {store.store_id}: сбой тика рассылок: {e}")


async def outbound_loop():
    """Фоновый цикл (аналог kaspi_chatbot_loop) — реже, чем чат-поллинг: у
    исходящих нет требования "ответить быстро", в отличие от входящих."""
    logger.info("📤 [Outbound] Фоновый воркер очереди рассылок инициализирован.")
    while True:
        try:
            await run_outbound_cycle()
        except Exception as e:
            logger.error(f"💥 [Outbound] Ошибка в фоновом цикле рассылок: {e}")
        await asyncio.sleep(60)
