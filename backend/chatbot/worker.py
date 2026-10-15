import time
import asyncio
import logging

from backend.chatbot import config, bot_instance, chat_client
from backend.chatbot import outbound as bot_outbound
from backend.chatbot.kaspi import load_chat_cookies, format_time, check_logistics_question, http_session
from backend.chatbot.ai import build_dialog, generate_ai_reply
from backend.chatbot.order_context import collect_chat_facts, render_facts_block
from backend.chatbot.prompt import (
    get_profile as get_bot_profile, find_human_triggers, find_escalation_reason, promises_manager, says_message_empty, STOCK_TRIGGERS,
)
from backend.state_helpers import _load_bot_sent_texts
from backend.chatbot.notifications import notify_startup, notify_cookie_expired, notify_manager_and_block, reset_cookie_alert
from backend.chatbot.handlers import register_handlers, register_bot_commands
from backend.dumper.config import discover_merchant_id
from backend.security.store_context import list_active_stores
from backend.services.ai_metrics import (
    record_chatbot_reply, observe_chatbot_ai_latency, record_chatbot_ai_reply,
)

logger = logging.getLogger(__name__)

# Сколько ждать тишины после последнего сообщения клиента, прежде чем
# отвечать: клиенты пишут очередью коротких реплик.
REPLY_DEBOUNCE_SECONDS = 15
# Сколько последних сообщений чата уходит модели и в проверку триггеров.
HISTORY_LIMIT = 20
# Размер страницы getDiffGroups. Kaspi отдаёт диалоги, изменённые после gts,
# от старых к новым, и флаг more, если есть ещё.
DIFF_PAGE_LIMIT = 50


def _pending_client_text(raw_history: list) -> str:
    """Всё, что клиент написал после последнего ответа магазина, одним текстом
    (sender="messenger" — покупатель, см. KASPI_CHAT_API.md)."""
    tail = []
    for m in reversed(raw_history or []):
        if m.get("sender") != "messenger":
            break
        text = (m.get("text") or (m.get("ext") or {}).get("text") or "").strip()
        if text:
            tail.append(text)
    return "\n".join(reversed(tail))


_MEDIA_TYPES = {"IMAGE": "фото", "VIDEO": "видео", "AUDIO": "голосовое"}
# Голосовые бот не эскалирует: на них отвечает модель по правилу 8 промпта
# (решение владельца 03.10.2026). Фото и видео — сразу менеджеру.
_MEDIA_HANDLED_BY_MODEL = {"голосовое"}


def _pending_client_media(raw_history: list) -> list:
    """Вложения клиента после последнего ответа магазина: фото/видео/голосовое,
    а также «вложение» (тип не распознан) и «пустое сообщение» (ни текста, ни
    известного типа). Бот их не видит, а на пустое отвечал клиенту «ваше
    сообщение пустое» — такие случаи тоже уходят менеджеру."""
    media = []
    for m in reversed(raw_history or []):
        if m.get("sender") != "messenger":
            break
        msg_type = str(m.get("messageType") or "").upper()
        kind = _MEDIA_TYPES.get(msg_type)
        if kind:
            media.append(kind)
        elif not (m.get("text") or (m.get("ext") or {}).get("text") or "").strip():
            media.append("пустое сообщение" if msg_type in ("", "TEXT") else "вложение")
    return media


# Per-store polling cursor (Phase 7) — store A and store B page through their
# own chat feed independently. store_id=None is the legacy single-tenant key.
_chatbot_state: dict = {}

def _get_chatbot_state(store_id=None) -> dict:
    if store_id not in _chatbot_state:
        day_start_ts = int(time.time() * 1000) - (24 * 3600 * 1000)
        _chatbot_state[store_id] = {"day_start_ts": day_start_ts, "start_gts": day_start_ts}
    return _chatbot_state[store_id]

# Один Telegram-алерт в день на магазин при исчерпании дневных кредитов
# (Слайс 3) — in-memory, ключ по (store_id, дата по Алматы) сам себя чистит:
# смена даты даёт новый ключ, процесс не растёт бесконечно за разумное время.
_credits_exhausted_alerted: set = set()

def _notify_credits_exhausted_once_daily(store_id: str) -> None:
    from backend.billing.credits import today_key
    key = (store_id, today_key())
    if key in _credits_exhausted_alerted:
        return
    _credits_exhausted_alerted.add(key)
    try:
        from backend.chatbot.notify_dispatch import dispatch
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(dispatch(store_id, "money", "⚠️ Дневной лимит ответов ИИ исчерпан, бот молчит до завтра.", urgent=True))
    except Exception as e:
        logger.debug(f"[Chatbot] notify_credits_exhausted skipped: {e}")

def _next_diff_cursor(groups: list, deferred_gts: list, current: int) -> int:
    """Куда сдвинуть gts после обработки страницы getDiffGroups: до самого
    свежего изменения на странице, но не дальше отложенных (debounce) групп."""
    if deferred_gts:
        return max(current, min(deferred_gts) - 1)
    page_max = max((g.get("gts") or 0 for g in groups), default=0)
    return max(current, page_max)

async def start_telegram_bot_polling():
    """
    Фоновый запуск Telegram бота (Aiogram polling).

    dp.start_polling() падает целиком при любом сетевом сбое (например,
    обрыв TG_BOT_PROXY) и раньше на этом всё заканчивалось — процесс
    оставался жив, но бот молча переставал получать апдейты насовсем
    (подтверждено вживую 2026-09-10: прокси упал на 1 минуту, polling
    умер и не поднимался почти сутки, пока прокси не проверили руками).
    Поэтому здесь цикл с перезапуском и паузой, чтобы временный сбой не
    убивал приём сообщений до следующего деплоя.
    """
    bot, dp = bot_instance.init_bot()
    if not (bot and dp):
        return
    register_handlers()
    await register_bot_commands()
    await notify_startup()
    while True:
        logger.info("🤖 [Telegram Bot] Polling запущен...")
        try:
            await dp.start_polling(bot, handle_signals=False)
            return
        except Exception as e:
            err_msg = str(e)
            if "Conflict" in err_msg or "TelegramConflictError" in err_msg:
                logger.warning("⚠️ [Telegram Bot] Бот уже запущен в параллельном процессе. Polling пропущен.")
                return
            logger.error(f"💥 [Telegram Bot] Ошибка polling: {e}. Перезапуск через 15с...")
            await asyncio.sleep(15)

async def _run_chatbot_pass(cookies: dict, merchant_id: str, store_id=None, token=None):
    """
    Один проход поллинга чата для одного магазина: тянет diff новых сообщений
    (курсор start_gts персистентен per-store между вызовами через
    _get_chatbot_state) и обрабатывает каждую новую реплику клиента.

    Возвращает True если проход завершился штатно (для решения о задержке
    следующего тика на уровне run_chatbot_cycle), False при HTTP-ошибке.

    store_id=None (легаси) или единственный реальный стор сегодня — merchant_id
    всегда резолвится динамически (discover_merchant_id), а НЕ берётся из
    Store.merchant_id в БД: это поле может быть синтетическим hash-фоллбеком
    (см. kaspi_wrapper.get_merchant_id), а не настоящим числовым ID Kaspi,
    который обязателен в заголовке x-merchant-id.
    """
    state = _get_chatbot_state(store_id)
    day_start_ts = state["day_start_ts"]
    groups_url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/group/getDiffGroups/chat"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://msg-web.kaspi.kz",
        "Referer": "https://msg-web.kaspi.kz/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-app-id": "475C3442-B742-4EE7-B10B-6738A0830492",
        "x-auth-type": "Webchat",
        "x-locale": "ru-RU",
        "x-merchant-id": merchant_id,
        "x-platform-type": "WEB"
    }

    payload = {"counters": [], "limit": DIFF_PAGE_LIMIT, "gts": state["start_gts"]}
    def fetch_diff():
        return http_session.post(groups_url, headers=headers, cookies=cookies, json=payload, timeout=8)

    resp = await asyncio.to_thread(fetch_diff)

    if resp.status_code in (401, 403):
        await notify_cookie_expired(f"Kaspi API вернул HTTP {resp.status_code}", store_id=store_id)
        return False

    if resp.status_code != 200:
        return False

    await asyncio.to_thread(reset_cookie_alert, store_id)

    data = resp.json().get("data", {})
    groups = data.get("groups", [])

    if not groups and data.get("sts"):
        state["start_gts"] = data.get("sts")

    # Диалоги, к которым вернёмся на следующем тике (клиент ещё печатает) —
    # курсор не должен уйти дальше них.
    deferred_gts: list[int] = []

    eligible_groups = [
        group for group in groups
        if group.get("id") != "chatGuide"
        and group.get("lastMessage", {}).get("created", 0) >= day_start_ts
    ]
    chat_states = await asyncio.to_thread(
        config.get_chat_states, store_id, [group.get("id") for group in eligible_groups if group.get("id")]
    )

    for group in eligible_groups:
        group_id = group.get("id")
        last_msg = group.get("lastMessage", {})
        msg_id = last_msg.get("id")
        msg_created = last_msg.get("created", 0)
        chat_state = chat_states.setdefault(group_id, {})
        if chat_state.get("human_override"):
            continue

        already_replied = chat_state.get("last_replied_msg_id") == msg_id

        if last_msg.get("sender") == "messenger" and not already_replied:
            # Клиент часто пишет несколькими сообщениями подряд. Пока последнее
            # совсем свежее — ждём: курсор не уйдёт дальше этой группы (см.
            # конец функции), она придёт и на следующем тике.
            if msg_created and time.time() * 1000 - msg_created < REPLY_DEBOUNCE_SECONDS * 1000:
                if group.get("gts"):
                    deferred_gts.append(group["gts"])
                continue

            client_name = group.get("subtitle") or group.get("title", "Клиент")
            item_title = group.get("title", "Товар")

            # История нужна и триггерам (всё, что клиент написал после
            # последнего ответа магазина, а не одна последняя реплика), и модели.
            raw_history = await chat_client.get_history(group_id, cookies, merchant_id, limit=HISTORY_LIMIT)
            client_text = _pending_client_text(raw_history) or last_msg.get("text", "")

            # Фаза 5 (кампания «Отзыв»): флаг одноразовый — снимаем сразу на
            # первом же ответе клиента, независимо от того, какой веткой
            # (human_triggers/логистика/ИИ) он в итоге обработается ниже.
            was_awaiting_review = bool(chat_state.get("awaiting_review_reply"))
            if was_awaiting_review:
                await asyncio.to_thread(config.clear_awaiting_review_reply, store_id, group_id, known_awaiting=True)
                chat_state["awaiting_review_reply"] = False

            # Фаза 6 (статистика рассылок): "клиент ответил" фиксируется тут,
            # ДО ветвления на human_triggers/логистику/ИИ — иначе негативные
            # ответы, уходящие в HUMAN_OVERRIDE, не попали бы в долю ответивших.
            if store_id:
                await asyncio.to_thread(bot_outbound.mark_replied_if_pending, store_id, group_id)

            bot_profile = await asyncio.to_thread(get_bot_profile, store_id)
            facts = await collect_chat_facts(store_id, token, group)

            async def _journal(kind: str, reply_text=None, detail=None, **extra):
                if not store_id:
                    return
                from backend.state_helpers import _append_chat_response_log
                await asyncio.to_thread(
                    _append_chat_response_log, store_id, group_id, kind=kind,
                    client_text=client_text, reply_text=reply_text,
                    order_code=facts.order_code, detail=detail, **extra,
                )
                record_chatbot_reply(kind, extra)

            async def _escalate(kind: str, detail: str):
                hold = await notify_manager_and_block(
                    group_id=group_id,
                    client_name=client_name,
                    item_title=item_title,
                    client_text=client_text,
                    cookies=cookies,
                    merchant_id=merchant_id,
                    msg_id=msg_id,
                    last_msg_created=msg_created,
                    store_id=store_id
                )
                await _journal(kind, reply_text=hold, detail=detail, billed_units=0)

            triggers = find_human_triggers(client_text, bot_profile.get("human_triggers"))
            # Вопрос только про наличие, а остаток товара известен из каталога —
            # отвечает бот, менеджер не нужен.
            if triggers and set(triggers) <= STOCK_TRIGGERS and facts.stock_known:
                triggers = []

            if triggers:
                await _escalate("inbound_escalated_trigger", "триггеры: " + ", ".join(triggers))
                continue

            reason = find_escalation_reason(client_text)
            if reason:
                await _escalate("inbound_escalated_trigger", f"жалоба/просьба: {reason}")
                continue

            pending_media = _pending_client_media(raw_history)
            media = [k for k in pending_media if k not in _MEDIA_HANDLED_BY_MODEL]
            if not client_text.strip() and not pending_media:
                media = ["пустое сообщение"]  # история не загрузилась или в ней нечего прочесть
            if media:
                await _escalate("inbound_escalated_trigger", "клиент прислал: " + ", ".join(sorted(set(media))))
                continue

            logistics_reply = check_logistics_question(client_text, bot_profile)
            # Шаблон говорит «мы передали заказ, дальше везёт Kaspi». Если по
            # данным заказа это неправда (ещё на сборке, самовывоз, заказа нет) —
            # отвечает модель, у которой есть статус заказа. Данные не
            # получили (сбой Kaspi) — шаблон, как раньше.
            if logistics_reply and (facts.order or not facts.order_code) and not facts.handed_to_courier:
                logistics_reply = None

            if logistics_reply:
                await asyncio.to_thread(config.mark_chat_replied, store_id, group_id, msg_id)
                chat_state["last_replied_msg_id"] = msg_id
                await chat_client.send_typing(group_id, logistics_reply + "\n", cookies, merchant_id)
                await asyncio.sleep(1.5)
                await chat_client.send_message(group_id, logistics_reply, cookies, merchant_id)
                await chat_client.mark_read(group_id, [{"id": msg_id, "created": last_msg.get("created")}], cookies, merchant_id)
                logger.info(f"✅ [Chatbot] Логистический шаблон отправлен клиенту {client_name}.")
                await _journal("inbound_logistics_template", reply_text=logistics_reply, billed_units=0)
                continue

            await asyncio.to_thread(config.mark_chat_replied, store_id, group_id, msg_id)
            chat_state["last_replied_msg_id"] = msg_id

            # Резерв дневного кредита ДО платного вызова DeepSeek (Слайс 3).
            # mark_chat_replied уже отработал выше — иначе 5с-цикл вечно
            # переоценивал бы то же сообщение при исчерпанном лимите.
            credit_units_milli = 0
            if store_id:
                from backend.billing import catalog as billing_catalog, credits as billing_credits
                band = bot_profile.get("prompt_tier") or billing_catalog.DEFAULT_PROMPT_BAND
                credit_units_milli = billing_catalog.multiplier_milli(band)
                consume = await asyncio.to_thread(billing_credits.try_consume, store_id, credit_units_milli)
                if not consume.ok:
                    logger.warning(f"[Chatbot] Store {store_id}: дневной лимит кредитов исчерпан, ответ клиенту {client_name} пропущен.")
                    await _journal("inbound_skipped_no_credits", billed_units=0)
                    await asyncio.to_thread(_notify_credits_exhausted_once_daily, store_id)
                    continue

            bot_texts, tracking_since = (set(), None)
            if store_id:
                bot_texts, tracking_since = await asyncio.to_thread(_load_bot_sent_texts, store_id, group_id)
            dialog = build_dialog(raw_history, bot_texts, tracking_since)
            ai_started = time.monotonic()
            try:
                ai_reply, is_human_override, ai_usage = await generate_ai_reply(
                    client_name, item_title, dialog, store_id=store_id, awaiting_review_reply=was_awaiting_review,
                    facts_block=render_facts_block(facts), client_text=client_text,
                )
            finally:
                observe_chatbot_ai_latency(time.monotonic() - ai_started)
            record_chatbot_ai_reply(facts)

            if is_human_override:
                await _escalate("inbound_escalated_ai", "модель: " + (ai_reply or "")[:180])
                continue

            if says_message_empty(ai_reply):
                await _escalate("inbound_escalated_ai", "модель не увидела сообщение: " + (ai_reply or "")[:150])
                continue

            promise = promises_manager(ai_reply)
            if promise:
                await _escalate("inbound_escalated_ai", f"модель обещала человека без метки ({promise}): " + (ai_reply or "")[:150])
                continue

            await chat_client.mark_read(group_id, [{"id": msg_id, "created": last_msg.get("created")}], cookies, merchant_id)
            await chat_client.send_typing(group_id, ai_reply + "\n", cookies, merchant_id)
            await asyncio.sleep(2)
            send_result = await chat_client.send_message(group_id, ai_reply, cookies, merchant_id)
            if send_result.get("success"):
                logger.info(f"✅ [Chatbot] ИИ-ответ успешно отправлен клиенту {client_name}!")
                await _journal(
                    "inbound_reply",
                    reply_text=ai_reply,
                    prompt_tier=bot_profile.get("prompt_tier", ""),
                    model=ai_usage.get("model", ""),
                    prompt_tokens=ai_usage.get("prompt_tokens"),
                    completion_tokens=ai_usage.get("completion_tokens"),
                    billed_units=1,
                    credit_units_milli=credit_units_milli,
                )
            else:
                logger.error(f"❌ [Chatbot] Сбой отправки ответа клиенту {client_name}: {send_result.get('error')}")
                if store_id and credit_units_milli:
                    from backend.billing import credits as billing_credits
                    await asyncio.to_thread(billing_credits.release, store_id, credit_units_milli)
                    await _journal("inbound_send_failed", reply_text=ai_reply, detail=str(send_result.get("error") or "")[:200], billed_units=0)

    # Kaspi отдаёт страницу от старых изменений к новым. Раньше курсор стоял на
    # месте, пока группы есть, — после первых 15 изменившихся за сутки диалогов
    # бот видел только их, а новые сообщения клиентов не видел до рестарта.
    if groups:
        state["start_gts"] = _next_diff_cursor(groups, deferred_gts, state["start_gts"])
        if data.get("more"):
            logger.info(f"[Chatbot] Store {store_id}: в diff есть ещё диалоги, дочитаю на следующем тике.")

    return True

async def run_chatbot_cycle():
    """
    Один тик мониторинга чатов по всем активным сторам (Phase 7). Возвращает
    задержку в секундах до следующего тика (сохраняет исходный тайминг:
    5с — выключено/успех, 10с — куки истекли).

    Слайс 4: флаг "enabled" теперь проверяется ОТДЕЛЬНО для каждого стора
    (ChatbotSettingsDB) — раньше один общий флаг выключал бота всем сразу.
    """
    stores = await list_active_stores()
    if stores:
        any_cookies = False
        any_enabled = False
        for store in stores:
            try:
                store_cfg = await asyncio.to_thread(config.load_chatbot_config, store.store_id)
                if not store_cfg.get("enabled", True):
                    continue
                from backend.billing import enforcement as billing_enforcement
                blocked, _reason = await asyncio.to_thread(billing_enforcement.is_service_blocked, store.store_id)
                if blocked:
                    continue
                any_enabled = True
                cookies = await asyncio.to_thread(load_chat_cookies, store_id=store.store_id)
                if not cookies:
                    logger.warning(f"[Chatbot] Store {store.store_id}: куки Kaspi MC отсутствуют/истекли, пропускаем проход.")
                    continue
                any_cookies = True
                merchant_id = await asyncio.to_thread(discover_merchant_id, store.token)
                if not merchant_id:
                    # НЕ подставляем чужой merchant_id (раньше был хардкод
                    # "20186986" — реальный ID Barkhan, из-за чего чат-проход
                    # для другого магазина тихо уходил под его личностью и
                    # ловил отказ от Kaspi). Пропускаем этот стор в этом тике.
                    logger.warning(f"[Chatbot] Store {store.store_id}: не удалось определить merchant_id, пропускаем проход.")
                    continue
                ok = await _run_chatbot_pass(cookies, merchant_id, store.store_id, token=store.token)
                if not ok:
                    return 10
            except Exception as e:
                logger.error(f"[Chatbot] Store {store.store_id} pass failed: {e}")
        if any_enabled and not any_cookies:
            await notify_cookie_expired("Куки Kaspi MC отсутствуют/истекли ни у одного активного стора", store_id=None)
            return 10
    else:
        cfg = await asyncio.to_thread(config.load_chatbot_config)
        if not cfg.get("enabled", True):
            return 5
        cookies = await asyncio.to_thread(load_chat_cookies, store_id=None)
        if not cookies:
            await notify_cookie_expired("Куки отсутствуют или истекли в states/session.json", store_id=None)
            return 10
        merchant_id = cfg.get("merchant_id")
        if not merchant_id:
            logger.warning("[Chatbot] Легаси-режим: не удалось определить merchant_id, пропускаем проход.")
            return 10
        ok = await _run_chatbot_pass(cookies, merchant_id, None)
        if not ok:
            return 10

    return 5

async def kaspi_chatbot_loop():
    """
    Основной фоновый асинхронный цикл мониторинга входящих сообщений Kaspi —
    по всем активным сторам (Phase 7).
    """
    logger.info("📡 [Kaspi Chatbot] Фоновый воркер автоответчика инициализирован.")

    while True:
        delay = 5
        try:
            delay = await run_chatbot_cycle()
        except Exception as e:
            logger.error(f"💥 [Chatbot] Ошибка в фоновом цикле: {e}")

        await asyncio.sleep(delay)
