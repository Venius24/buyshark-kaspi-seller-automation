import asyncio
import logging
from datetime import datetime
from typing import Optional

from backend.chatbot import config, bot_instance, notify_dispatch, chat_client

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

    if not bot_instance.bot:
        logger.warning(f"🚨 [COOKIE ALERT] {reason}. (Telegram-бот выключен/не настроен)")
        config.set_cookie_alert_sent(store_id, True)
        return

    store_name = config.get_store_name(store_id) if store_id else None
    store_line = f"🏪 *Магазин:* {_esc_md(store_name)}\n" if store_name else ""
    alert_text = (
        f"🚨 *ВНИМАНИЕ: СЕССИЯ ЧАТА KASPI ИСТЕКЛА!*\n"
        f"----------------------------------------\n"
        f"{store_line}"
        f"⚠️ *Причина:* {_esc_md(reason)}\n"
        f"⏰ *Время:* {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
        f"----------------------------------------\n"
        f"🛠 *Действие:* Зайдите в дашборд → вкладка «ИИ-Автоответчик» и повторно войдите по номеру телефона (SMS-код). "
        f"Это отдельный вход от основного кабинета — email/пароль тут не поможет."
    )

    if store_id is not None:
        logger.warning(f"🚨 [COOKIE ALERT] {reason}.")
        await notify_dispatch.dispatch(store_id, "problems", alert_text, urgent=True, card="cookie_expired")
    else:
        logger.warning(f"🚨 [COOKIE ALERT] {reason}. (без store_id — некому отправлять, привязка per-store)")

    config.set_cookie_alert_sent(store_id, True)

async def notify_startup():
    """Логирует запуск бота. Раньше слала Telegram-алерт на легаси admin_ids
    (см. HANDOFF, вырезано 2026-08-09) — старт бота не привязан к
    конкретному магазину, а per-store уведомления реальным пользователям
    (notify_dispatch) для этого события не годятся, поэтому теперь это
    просто запись в лог для journalctl."""
    cfg = config.load_chatbot_config()
    merchant_id = cfg.get("merchant_id") or "неизвестен"
    logger.info(f"🚀 Бот успешно запущен и начал работу! Merchant ID (легаси .env): {merchant_id}")

async def notify_manager_and_block(
    group_id: str, client_name: str, item_title: str, client_text: str,
    cookies: dict, merchant_id: str, msg_id: str, last_msg_created: int,
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

    try:
        await chat_client.send_typing(group_id, hold_reply + "\n", cookies, merchant_id)
        await asyncio.sleep(1)
        await chat_client.send_message(group_id, hold_reply, cookies, merchant_id)
        await chat_client.mark_read(group_id, [{"id": msg_id, "created": last_msg_created}], cookies, merchant_id)
        logger.info(f"💬 Клиенту {client_name} отправлен Hold-ответ: \"{hold_reply[:30]}...\"")
    except Exception as e:
        logger.error(f"⚠️ Ошибка отправки Hold-ответа в Kaspi: {e}")

    web_link = f"https://msg-web.kaspi.kz/#/chats/chat/{group_id}"
    pay_link = f"https://pay.kaspi.kz/chat?threadId={group_id}&isWeb=true"

    store_name = config.get_store_name(store_id) if store_id else None
    store_line = f"🏪 *Магазин:* {_esc_md(store_name)}\n" if store_name else ""
    alert_text = (
        f"🚨 *СРОЧНЫЙ ВЫЗОВ МЕНЕДЖЕРА В КАСПИ!*\n"
        f"----------------------------------------\n"
        f"{store_line}"
        f"👤 *Клиент:* {_esc_md(client_name)}\n"
        f"📦 *Товар:* {_esc_md(item_title)}\n"
        f"💬 *Проблема/Запрос:* \"{_esc_md(client_text)}\"\n"
        f"----------------------------------------\n"
        f"🔗 [Открыть в ЛК Веб]({web_link})\n"
        f"📱 [Открыть через Pay-ссылку]({pay_link})\n"
    )

    if bot_instance.bot and bot_instance.HAS_AIOGRAM:
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✍️ Ответить", callback_data=f"chat_open:{group_id}")],
                [InlineKeyboardButton(text="✅ Разблокировать для ИИ", callback_data=f"unblock_{group_id}")],
            ])

            if store_id is not None:
                await notify_dispatch.dispatch(store_id, "chats", alert_text, urgent=True, reply_markup=keyboard, card="chat_alert")
        except Exception as e:
            logger.error(f"⚠️ Ошибка формирования Telegram клавиатуры: {e}")

    config.set_chat_human_override(store_id, group_id, client_name=client_name)
