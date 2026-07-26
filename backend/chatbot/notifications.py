import time
import uuid
import asyncio
import logging
from datetime import datetime

from backend.chatbot import config, bot_instance

logger = logging.getLogger(__name__)

def reset_cookie_alert():
    """Сбрасывает флаг предупреждения при успешном запросе с валидными куками."""
    if config.COOKIE_ALERT_SENT:
        logger.info("✅ Сессия Kaspi восстановлена. Куки снова валидны!")
        config.COOKIE_ALERT_SENT = False

async def notify_cookie_expired(reason: str):
    """
    Отправляет алерт о невалидных/просроченных куках в Telegram.
    Отправка происходит единоразово с предотвращением спама.
    """
    if config.COOKIE_ALERT_SENT:
        return

    admin_ids = config.load_admin_ids()
    if not bot_instance.bot:
        logger.warning(f"🚨 [COOKIE ALERT] {reason}. (Telegram-бот выключен/не настроен)")
        config.COOKIE_ALERT_SENT = True
        return

    cfg = config.load_chatbot_config()
    merchant_id = cfg.get("merchant_id", "20186986")
    alert_text = (
        f"🚨 **ВНИМАНИЕ: КУКИ KASPI НЕВАЛИДНЫ / СЕССИЯ ИСТЕКЛА!**\n"
        f"----------------------------------------\n"
        f"⚠️ **Причина:** {reason}\n"
        f"⏰ **Время:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"----------------------------------------\n"
        f"🛠 **Действие:** Пожалуйста, обновите `session.json` с актуальными куки `mc-session` и `mc-sid`!"
    )

    logger.warning(f"🚨 [COOKIE ALERT] {reason}. Отправка уведомления на Telegram IDs: {admin_ids}")

    for admin_id in admin_ids:
        try:
            await bot_instance.bot.send_message(
                chat_id=admin_id,
                text=alert_text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"⚠️ Ошибка отправки алерта админу {admin_id}: {e}")

    config.COOKIE_ALERT_SENT = True

async def notify_startup():
    """Отправляет уведомление о запуске бота в Telegram всем администраторам."""
    admin_ids = config.load_admin_ids()
    if not bot_instance.bot:
        return

    cfg = config.load_chatbot_config()
    merchant_id = cfg.get("merchant_id", "20186986")
    alert_text = (
        f"🚀 **Бот успешно запущен и начал работу!**\n"
        f"----------------------------------------\n"
        f"⏰ **Время:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"🏪 **Merchant ID:** `{merchant_id}`"
    )

    logger.info(f"🚀 Отправка уведомления о запуске бота на Telegram IDs: {admin_ids}")
    for admin_id in admin_ids:
        try:
            await bot_instance.bot.send_message(
                chat_id=admin_id,
                text=alert_text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"⚠️ Не удалось отправить сообщение о запуске админу {admin_id}: {e}")

async def notify_manager_and_block(
    group_id: str, client_name: str, item_title: str, client_text: str,
    cookies: dict, headers: dict, msg_id: str, last_msg_created: int
):
    """
    Отправляет успокаивающий ответ клиенту в Kaspi,
    затем шлет алерт в Telegram и блокирует чат для ИИ (HUMAN_OVERRIDE).
    """
    clean_text = client_text.lower()
    is_kz = any(word in clean_text for word in ['ката', 'кате', 'бузык', 'істемейді', 'қайтару', 'шағым', 'бар ма', 'бар ма?', 'қалды'])
    
    if is_kz:
        hold_reply = "Өтінішіңізді қабылдадық. Қазір дүкен менеджері сұрағыңызды нақтылап, жағдайды толық тексереді. Сәл күте тұруыңызды сұраймыз."
    else:
        hold_reply = "Приняли ваше обращение. Сейчас менеджер магазина уточнит ваш запрос и во всем разберется. Пожалуйста, ожидайте."

    status_url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/messageStatus/changeStatus"
    typing_url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/typing/sendText"
    send_url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/messages/sendMessage"

    try:
        def send_kaspi_hold():
            from backend.chatbot.kaspi import http_session
            http_session.post(typing_url, headers=headers, cookies=cookies, json={"groupId": group_id, "text": hold_reply + "\n"}, timeout=5)
            time.sleep(1)
            
            send_payload = {
                "created": int(time.time() * 1000),
                "data": {"text": hold_reply},
                "messageId": str(uuid.uuid4()).upper(),
                "groupId": group_id,
                "transitionContextUrl": f"https://pay.kaspi.kz/chat?threadId={group_id}&isWeb=true"
            }
            http_session.post(send_url, headers=headers, cookies=cookies, json=send_payload, timeout=5)
            
            http_session.post(status_url, headers=headers, cookies=cookies, json={
                "groupId": group_id, "messages": [{"id": msg_id, "created": last_msg_created}]
            }, timeout=5)

        await asyncio.to_thread(send_kaspi_hold)
        logger.info(f"💬 Клиенту {client_name} отправлен Hold-ответ: \"{hold_reply[:30]}...\"")
    except Exception as e:
        logger.error(f"⚠️ Ошибка отправки Hold-ответа в Kaspi: {e}")

    web_link = f"https://msg-web.kaspi.kz/#/chats/chat/{group_id}"
    pay_link = f"https://pay.kaspi.kz/chat?threadId={group_id}&isWeb=true"

    alert_text = (
        f"🚨 **СРОЧНЫЙ ВЫЗОВ МЕНЕДЖЕРА В КАСПИ!**\n"
        f"----------------------------------------\n"
        f"👤 **Клиент:** {client_name}\n"
        f"📦 **Товар:** {item_title}\n"
        f"💬 **Проблема/Запрос:** \"{client_text}\"\n"
        f"----------------------------------------\n"
        f"🔗 [Открыть в ЛК Веб]({web_link})\n"
        f"📱 [Открыть через Pay-ссылку]({pay_link})\n"
    )

    admin_ids = config.load_admin_ids()
    if bot_instance.bot and bot_instance.HAS_AIOGRAM:
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Разблокировать для ИИ", callback_data=f"unblock_{group_id}")]
            ])
            for admin_id in admin_ids:
                try:
                    await bot_instance.bot.send_message(
                        chat_id=admin_id,
                        text=alert_text,
                        parse_mode="Markdown",
                        disable_web_page_preview=True,
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logger.error(f"⚠️ Не удалось отправить алерт вызова менеджеру админу {admin_id}: {e}")
        except Exception as e:
            logger.error(f"⚠️ Ошибка формирования Telegram клавиатуры: {e}")

    config.PROCESSED_CHATS[group_id] = "HUMAN_OVERRIDE"
