import time
import uuid
import asyncio
import logging

from backend.chatbot import config, bot_instance
from backend.chatbot.kaspi import load_cookies, format_time, check_logistics_question, get_chat_history, http_session
from backend.chatbot.ai import generate_ai_reply
from backend.chatbot.notifications import notify_startup, notify_cookie_expired, notify_manager_and_block, reset_cookie_alert
from backend.chatbot.handlers import register_handlers

logger = logging.getLogger(__name__)

async def start_telegram_bot_polling():
    """Фоновый запуск Telegram бота (Aiogram polling)."""
    bot, dp = bot_instance.init_bot()
    if bot and dp:
        register_handlers()
        await notify_startup()
        logger.info("🤖 [Telegram Bot] Polling запущен...")
        try:
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"💥 [Telegram Bot] Ошибка polling: {e}")

async def kaspi_chatbot_loop():
    """
    Основной фоновый асинхронный цикл мониторинга входящих сообщений Kaspi.
    Проверяет master-switch `enabled` из конфигурации.
    """
    logger.info("📡 [Kaspi Chatbot] Фоновый воркер автоответчика инициализирован.")
    
    day_start_ts = int(time.time() * 1000) - (24 * 3600 * 1000)
    start_gts = day_start_ts
    groups_url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/group/getDiffGroups/chat"

    while True:
        try:
            cfg = config.load_chatbot_config()
            
            # Если чат-бот отключен пользователем через интерфейс дашборда
            if not cfg.get("enabled", True):
                await asyncio.sleep(5)
                continue

            cookies = load_cookies()
            if not cookies:
                await notify_cookie_expired("Куки отсутствуют или истекли в states/session.json")
                await asyncio.sleep(10)
                continue

            merchant_id = cfg.get("merchant_id", "20186986")
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

            payload = {"counters": [], "limit": 15, "gts": start_gts}
            def fetch_diff():
                return http_session.post(groups_url, headers=headers, cookies=cookies, json=payload, timeout=8)
            
            resp = await asyncio.to_thread(fetch_diff)

            if resp.status_code in (401, 403):
                await notify_cookie_expired(f"Kaspi API вернул HTTP {resp.status_code}")
                await asyncio.sleep(10)
                continue

            if resp.status_code != 200:
                await asyncio.sleep(5)
                continue

            reset_cookie_alert()

            data = resp.json().get("data", {})
            groups = data.get("groups", [])
            
            if not groups and data.get("sts"):
                start_gts = data.get("sts")

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

        except Exception as e:
            logger.error(f"💥 [Chatbot] Ошибка в фоновом цикле: {e}")

        await asyncio.sleep(5)
