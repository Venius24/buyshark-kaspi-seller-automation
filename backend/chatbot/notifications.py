import time
import uuid
import asyncio
import logging
from datetime import datetime
from typing import Optional

from backend.chatbot import config, bot_instance

logger = logging.getLogger(__name__)

def _esc_md(text) -> str:
    """
    Экранирует спецсимволы legacy Telegram Markdown (_ * ` [) в значениях,
    подставляемых в текст алерта. Без этого один незакрытый '_' в
    merchant_id/имени клиента/тексте сообщения роняет всю отправку
    ("can't parse entities") — подтверждено вживую на 'm_87776f40'.
    """
    text = str(text)
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text

def reset_cookie_alert(store_id: Optional[str] = None):
    """Сбрасывает флаг предупреждения при успешном запросе с валидными куками."""
    if config.is_cookie_alert_sent(store_id):
        logger.info("✅ Сессия Kaspi восстановлена. Куки снова валидны!")
        config.set_cookie_alert_sent(store_id, False)

async def notify_cookie_expired(reason: str, store_id: Optional[str] = None):
    """
    Отправляет алерт о невалидных/просроченных куках в Telegram админам
    ТОГО магазина, где это произошло (Слайс 4). Отправка единоразовая с
    предотвращением спама — per-store.
    """
    if config.is_cookie_alert_sent(store_id):
        return

    admin_ids = config.load_admin_ids(store_id)
    if not bot_instance.bot:
        logger.warning(f"🚨 [COOKIE ALERT] {reason}. (Telegram-бот выключен/не настроен)")
        config.set_cookie_alert_sent(store_id, True)
        return

    store_name = config.get_store_name(store_id) if store_id else None
    store_line = f"🏪 **Магазин:** {_esc_md(store_name)}\n" if store_name else ""
    alert_text = (
        f"🚨 **ВНИМАНИЕ: СЕССИЯ ЧАТА KASPI ИСТЕКЛА!**\n"
        f"----------------------------------------\n"
        f"{store_line}"
        f"⚠️ **Причина:** {_esc_md(reason)}\n"
        f"⏰ **Время:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"----------------------------------------\n"
        f"🛠 **Действие:** Зайдите в дашборд → вкладка «ИИ-Автоответчик» и повторно войдите по номеру телефона (SMS-код). "
        f"Это отдельный вход от основного кабинета — email/пароль тут не поможет."
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

    config.set_cookie_alert_sent(store_id, True)

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
        f"🏪 **Merchant ID:** `{_esc_md(merchant_id)}`"
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
    cookies: dict, headers: dict, msg_id: str, last_msg_created: int,
    store_id: Optional[str] = None
):
    """
    Отправляет успокаивающий ответ клиенту в Kaspi, затем шлет алерт в
    Telegram (админам ТОГО магазина, где пришёл этот чат — Слайс 4) и
    блокирует чат для ИИ (HUMAN_OVERRIDE), per-store.
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

    store_name = config.get_store_name(store_id) if store_id else None
    store_line = f"🏪 **Магазин:** {_esc_md(store_name)}\n" if store_name else ""
    alert_text = (
        f"🚨 **СРОЧНЫЙ ВЫЗОВ МЕНЕДЖЕРА В КАСПИ!**\n"
        f"----------------------------------------\n"
        f"{store_line}"
        f"👤 **Клиент:** {_esc_md(client_name)}\n"
        f"📦 **Товар:** {_esc_md(item_title)}\n"
        f"💬 **Проблема/Запрос:** \"{_esc_md(client_text)}\"\n"
        f"----------------------------------------\n"
        f"🔗 [Открыть в ЛК Веб]({web_link})\n"
        f"📱 [Открыть через Pay-ссылку]({pay_link})\n"
    )

    admin_ids = config.load_admin_ids(store_id)
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

    config.set_chat_human_override(store_id, group_id)
