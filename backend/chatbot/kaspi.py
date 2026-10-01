import os
import re
import json
import asyncio
import logging
import requests
from datetime import datetime
from backend.chatbot.config import SESSION_FILE, LOGISTICS_TRIGGERS
from backend.chatbot.notifications import notify_cookie_expired, reset_cookie_alert

logger = logging.getLogger(__name__)

# Отдельная HTTP сессия для Kaspi API без глобальных прокси
http_session = requests.Session()

def load_cookies(store_id: str | None = None) -> dict:
    """
    Унифицированная загрузка кук mc-session и mc-sid. store_id=None (легаси)
    читает общие state.json/session.json; store_id given — исключительно
    куки этого стора из БД, без фоллбека.
    """
    from backend.state_helpers import _load_mc_cookies as _unified_load_mc_cookies
    cookies = _unified_load_mc_cookies(store_id=store_id)
    if not cookies or "mc-session" not in cookies or "mc-sid" not in cookies:
        logger.warning(f"⚠️ Куки 'mc-session'/'mc-sid' отсутствуют для стора {store_id!r}.")
        return {}
    return cookies

def load_chat_cookies(store_id: str | None = None) -> dict:
    """
    Куки для чат-API Kaspi — отдельная сессия от общего кабинета, полученная
    через телефон+SMS вход (см. state_helpers._load_chat_cookies).
    """
    from backend.state_helpers import _load_chat_cookies
    cookies = _load_chat_cookies(store_id=store_id)
    if not cookies or "mc-session" not in cookies or "mc-sid" not in cookies:
        logger.warning(f"⚠️ Куки чата Kaspi (SMS-сессия) отсутствуют для стора {store_id!r}.")
        return {}
    return cookies


def format_time(ts_ms) -> str:
    if not ts_ms:
        return "--:--"
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000).strftime('%d.%m %H:%M:%S')
    except Exception:
        return "--:--"

def check_logistics_question(text: str, profile: dict | None = None) -> str | None:
    """Проверяет текст на ключевые слова доставки и возвращает готовый шаблон.
    Триггерные слова общие (kk/ru), сам текст ответа берётся из профиля этого
    магазина (backend/chatbot/prompt.py), если он передан — иначе дефолт."""
    if not text:
        return None
    from backend.chatbot.prompt import looks_like_complaint
    # Жалоба с упоминанием курьера/доставки — не логистика: шаблон «пишите в
    # Kaspi Гид» на «пришёл сломанный» отправлял клиента не туда.
    if looks_like_complaint(text):
        return None
    clean_text = text.lower()
    reply_kk = (profile or {}).get("logistics_reply_kk") or LOGISTICS_TRIGGERS["kk"]["reply"]
    reply_ru = (profile or {}).get("logistics_reply_ru") or LOGISTICS_TRIGGERS["ru"]["reply"]

    def _has(keywords) -> bool:
        # От начала слова, а не подстрокой где угодно.
        return any(re.search(r'(?<!\w)' + re.escape(word), clean_text) for word in keywords)

    if _has(LOGISTICS_TRIGGERS["kk"]["keywords"]):
        return reply_kk
    if _has(LOGISTICS_TRIGGERS["ru"]["keywords"]):
        return reply_ru
    return None

async def get_chat_history(group_id: str, cookies: dict, headers: dict) -> list:
    """Загружает историю сообщений чата из Kaspi API."""
    url = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1/history/loadMoreMessages"
    payload = {"limit": 15, "groupId": group_id}
    try:
        def fetch():
            return http_session.post(url, headers=headers, cookies=cookies, json=payload, timeout=8)
        resp = await asyncio.to_thread(fetch)
        if resp.status_code in (401, 403):
            await notify_cookie_expired(f"HTTP {resp.status_code} при запросе истории диалога {group_id}")
            return []
        if resp.status_code == 200:
            reset_cookie_alert()
            messages_raw = resp.json().get("data", {}).get("messages", [])
            messages_raw.sort(key=lambda x: x.get("created", 0))
            return [f"{'Клиент' if m.get('sender') == 'messenger' else 'Магазин'}: {m.get('text', '')}" for m in messages_raw]
    except Exception as e:
        logger.error(f"⚠️ Ошибка загрузки истории для чата {group_id}: {e}")
    return []
