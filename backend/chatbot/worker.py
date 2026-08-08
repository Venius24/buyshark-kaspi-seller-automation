import time
import asyncio
import logging

from backend.chatbot import config, bot_instance, chat_client
from backend.chatbot import outbound as bot_outbound
from backend.chatbot.kaspi import load_chat_cookies, format_time, check_logistics_question, get_chat_history, http_session
from backend.chatbot.ai import generate_ai_reply
from backend.chatbot.prompt import get_profile as get_bot_profile, match_human_trigger
from backend.chatbot.notifications import notify_startup, notify_cookie_expired, notify_manager_and_block, reset_cookie_alert
from backend.chatbot.handlers import register_handlers, register_bot_commands
from backend.dumper.config import discover_merchant_id
from backend.security.store_context import list_active_stores

logger = logging.getLogger(__name__)

# Per-store polling cursor (Phase 7) — store A and store B page through their
# own chat feed independently. store_id=None is the legacy single-tenant key.
_chatbot_state: dict = {}

def _get_chatbot_state(store_id=None) -> dict:
    if store_id not in _chatbot_state:
        day_start_ts = int(time.time() * 1000) - (24 * 3600 * 1000)
        _chatbot_state[store_id] = {"day_start_ts": day_start_ts, "start_gts": day_start_ts}
    return _chatbot_state[store_id]

async def start_telegram_bot_polling():
    """Фоновый запуск Telegram бота (Aiogram polling)."""
    bot, dp = bot_instance.init_bot()
    if bot and dp:
        register_handlers()
        await register_bot_commands()
        await notify_startup()
        logger.info("🤖 [Telegram Bot] Polling запущен...")
        try:
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            err_msg = str(e)
            if "Conflict" in err_msg or "TelegramConflictError" in err_msg:
                logger.warning("⚠️ [Telegram Bot] Бот уже запущен в параллельном процессе. Polling пропущен.")
            else:
                logger.error(f"💥 [Telegram Bot] Ошибка polling: {e}")

async def _run_chatbot_pass(cookies: dict, merchant_id: str, store_id=None):
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

    payload = {"counters": [], "limit": 15, "gts": state["start_gts"]}
    def fetch_diff():
        return http_session.post(groups_url, headers=headers, cookies=cookies, json=payload, timeout=8)

    resp = await asyncio.to_thread(fetch_diff)

    if resp.status_code in (401, 403):
        await notify_cookie_expired(f"Kaspi API вернул HTTP {resp.status_code}", store_id=store_id)
        return False

    if resp.status_code != 200:
        return False

    reset_cookie_alert(store_id)

    data = resp.json().get("data", {})
    groups = data.get("groups", [])

    if not groups and data.get("sts"):
        state["start_gts"] = data.get("sts")

    for group in groups:
        group_id = group.get("id")
        if group_id == "chatGuide":
            continue

        last_msg = group.get("lastMessage", {})
        msg_id = last_msg.get("id")
        msg_created = last_msg.get("created", 0)

        if msg_created < day_start_ts:
            continue

        if config.is_chat_human_override(store_id, group_id):
            continue

        already_replied = config.get_chat_last_replied(store_id, group_id) == msg_id

        if last_msg.get("sender") == "messenger" and not already_replied:
            client_name = group.get("subtitle") or group.get("title", "Клиент")
            item_title = group.get("title", "Товар")
            client_text = last_msg.get("text", "")

            # Фаза 5 (кампания «Отзыв»): флаг одноразовый — снимаем сразу на
            # первом же ответе клиента, независимо от того, какой веткой
            # (human_triggers/логистика/ИИ) он в итоге обработается ниже.
            was_awaiting_review = config.is_awaiting_review_reply(store_id, group_id)
            if was_awaiting_review:
                await asyncio.to_thread(config.clear_awaiting_review_reply, store_id, group_id)

            # Фаза 6 (статистика рассылок): "клиент ответил" фиксируется тут,
            # ДО ветвления на human_triggers/логистику/ИИ — иначе негативные
            # ответы, уходящие в HUMAN_OVERRIDE, не попали бы в долю ответивших.
            if store_id:
                await asyncio.to_thread(bot_outbound.mark_replied_if_pending, store_id, group_id)

            bot_profile = await asyncio.to_thread(get_bot_profile, store_id)
            need_human = match_human_trigger(client_text, bot_profile.get("human_triggers"))

            if need_human:
                await notify_manager_and_block(
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
                continue

            logistics_reply = check_logistics_question(client_text, bot_profile)

            if logistics_reply:
                config.mark_chat_replied(store_id, group_id, msg_id)
                await chat_client.send_typing(group_id, logistics_reply + "\n", cookies, merchant_id)
                await asyncio.sleep(1.5)
                await chat_client.send_message(group_id, logistics_reply, cookies, merchant_id)
                await chat_client.mark_read(group_id, [{"id": msg_id, "created": last_msg.get("created")}], cookies, merchant_id)
                logger.info(f"✅ [Chatbot] Логистический шаблон отправлен клиенту {client_name}.")
                if store_id:
                    from backend.state_helpers import _append_chat_response_log
                    _append_chat_response_log(store_id, group_id, kind="inbound_logistics_template", billed_units=0)
                continue

            config.mark_chat_replied(store_id, group_id, msg_id)
            history = await get_chat_history(group_id, cookies, headers)
            ai_reply, is_human_override, ai_usage = await generate_ai_reply(
                client_name, item_title, history, store_id=store_id, awaiting_review_reply=was_awaiting_review
            )

            if is_human_override:
                await notify_manager_and_block(
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
                continue

            await chat_client.mark_read(group_id, [{"id": msg_id, "created": last_msg.get("created")}], cookies, merchant_id)
            await chat_client.send_typing(group_id, ai_reply + "\n", cookies, merchant_id)
            await asyncio.sleep(2)
            send_result = await chat_client.send_message(group_id, ai_reply, cookies, merchant_id)
            if send_result.get("success"):
                logger.info(f"✅ [Chatbot] ИИ-ответ успешно отправлен клиенту {client_name}!")
                if store_id:
                    from backend.state_helpers import _append_chat_response_log
                    _append_chat_response_log(
                        store_id, group_id,
                        kind="inbound_reply",
                        prompt_tier=bot_profile.get("prompt_tier", ""),
                        model=ai_usage.get("model", ""),
                        prompt_tokens=ai_usage.get("prompt_tokens"),
                        completion_tokens=ai_usage.get("completion_tokens"),
                        billed_units=1,
                    )
            else:
                logger.error(f"❌ [Chatbot] Сбой отправки ответа клиенту {client_name}: {send_result.get('error')}")

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
                ok = await _run_chatbot_pass(cookies, merchant_id, store.store_id)
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
