import time
import uuid
import asyncio
import logging

from backend.chatbot import config, bot_instance
from backend.chatbot.kaspi import load_cookies, format_time, check_logistics_question, get_chat_history, http_session
from backend.chatbot.ai import generate_ai_reply
from backend.chatbot.notifications import notify_startup, notify_cookie_expired, notify_manager_and_block, reset_cookie_alert
from backend.chatbot.handlers import register_handlers
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
        await notify_cookie_expired(f"Kaspi API вернул HTTP {resp.status_code}")
        return False

    if resp.status_code != 200:
        return False

    reset_cookie_alert()

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

        if config.PROCESSED_CHATS.get(group_id) == "HUMAN_OVERRIDE":
            continue

        already_replied = config.PROCESSED_CHATS.get(group_id) == msg_id

        if last_msg.get("sender") == "messenger" and not already_replied:
            client_name = group.get("subtitle") or group.get("title", "Клиент")
            item_title = group.get("title", "Товар")
            client_text = last_msg.get("text", "")

            human_triggers = [
                'брак', 'сломан', 'не работает', 'суд', 'жалоба', 'гид', 'ошибка', 'человек', 'менеджер', 'верните', 'разбит',
                'есть ли', 'в наличии', 'остаток', 'остатки', 'количество', 'штук', 'шт', 'бар ма', 'бар ма?', 'қалды', 'обмен'
            ]
            need_human = any(word in client_text.lower() for word in human_triggers)

            if need_human:
                await notify_manager_and_block(
                    group_id=group_id,
                    client_name=client_name,
                    item_title=item_title,
                    client_text=client_text,
                    cookies=cookies,
                    headers=headers,
                    msg_id=msg_id,
                    last_msg_created=msg_created
                )
                continue

            logistics_reply = check_logistics_question(client_text)

            status_url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/messageStatus/changeStatus"
            typing_url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/typing/sendText"
            send_url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/messages/sendMessage"

            if logistics_reply:
                config.PROCESSED_CHATS[group_id] = msg_id
                def send_logistics():
                    http_session.post(typing_url, headers=headers, cookies=cookies, json={"groupId": group_id, "text": logistics_reply + "\n"}, timeout=5)
                    time.sleep(1.5)

                    send_payload = {
                        "created": int(time.time() * 1000),
                        "data": {"text": logistics_reply},
                        "messageId": str(uuid.uuid4()).upper(),
                        "groupId": group_id,
                        "transitionContextUrl": f"https://pay.kaspi.kz/chat?threadId={group_id}&isWeb=true"
                    }
                    http_session.post(send_url, headers=headers, cookies=cookies, json=send_payload, timeout=5)

                    http_session.post(status_url, headers=headers, cookies=cookies, json={
                        "groupId": group_id, "messages": [{"id": msg_id, "created": last_msg.get("created")}]
                    }, timeout=5)

                await asyncio.to_thread(send_logistics)
                logger.info(f"✅ [Chatbot] Логистический шаблон отправлен клиенту {client_name}.")
                continue

            config.PROCESSED_CHATS[group_id] = msg_id
            history = await get_chat_history(group_id, cookies, headers)
            ai_reply, is_human_override = await generate_ai_reply(client_name, item_title, history)

            if is_human_override:
                await notify_manager_and_block(
                    group_id=group_id,
                    client_name=client_name,
                    item_title=item_title,
                    client_text=client_text,
                    cookies=cookies,
                    headers=headers,
                    msg_id=msg_id,
                    last_msg_created=msg_created
                )
                continue

            def send_ai_response():
                http_session.post(status_url, headers=headers, cookies=cookies, json={
                    "groupId": group_id,
                    "messages": [{"id": msg_id, "created": last_msg.get("created")}]
                }, timeout=5)

                http_session.post(typing_url, headers=headers, cookies=cookies, json={"groupId": group_id, "text": ai_reply + "\n"}, timeout=5)
                time.sleep(2)

                send_payload = {
                    "created": int(time.time() * 1000),
                    "data": {"text": ai_reply},
                    "messageId": str(uuid.uuid4()).upper(),
                    "groupId": group_id,
                    "transitionContextUrl": f"https://pay.kaspi.kz/chat?threadId={group_id}&isWeb=true"
                }

                return http_session.post(send_url, headers=headers, cookies=cookies, json=send_payload, timeout=5)

            send_resp = await asyncio.to_thread(send_ai_response)
            if send_resp.status_code == 200 and send_resp.json().get("StatusCode") == 0:
                logger.info(f"✅ [Chatbot] ИИ-ответ успешно отправлен клиенту {client_name}!")
            else:
                logger.error(f"❌ [Chatbot] Сбой отправки ответа клиенту {client_name}: {send_resp.text}")

    return True

async def run_chatbot_cycle():
    """
    Один тик мониторинга чатов по всем активным сторам (Phase 7). Возвращает
    задержку в секундах до следующего тика (сохраняет исходный тайминг:
    5с — выключено/успех, 10с — куки истекли).
    """
    cfg = config.load_chatbot_config()

    # Если чат-бот отключен пользователем через интерфейс дашборда
    if not cfg.get("enabled", True):
        return 5

    stores = await list_active_stores()
    if stores:
        any_cookies = False
        for store in stores:
            try:
                cookies = load_cookies(store_id=store.store_id)
                if not cookies:
                    logger.warning(f"[Chatbot] Store {store.store_id}: куки Kaspi MC отсутствуют/истекли, пропускаем проход.")
                    continue
                any_cookies = True
                merchant_id = discover_merchant_id(store.token) or cfg.get("merchant_id", "20186986")
                ok = await _run_chatbot_pass(cookies, merchant_id, store.store_id)
                if not ok:
                    return 10
            except Exception as e:
                logger.error(f"[Chatbot] Store {store.store_id} pass failed: {e}")
        if not any_cookies:
            await notify_cookie_expired("Куки Kaspi MC отсутствуют/истекли ни у одного активного стора")
            return 10
    else:
        cookies = load_cookies(store_id=None)
        if not cookies:
            await notify_cookie_expired("Куки отсутствуют или истекли в states/session.json")
            return 10
        merchant_id = cfg.get("merchant_id", "20186986")
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
