import os
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

def load_cookies() -> dict:
    """
    Загружает куки mc-session и mc-sid из states/session.json.
    Совместимо с форматом PlaywrightWorker и state.json.
    """
    if not os.path.exists(SESSION_FILE):
        # Fallback на states/state.json если session.json отсутствует
        alt_file = os.path.join(os.path.dirname(SESSION_FILE), "state.json")
        if os.path.exists(alt_file):
            target_file = alt_file
        else:
            logger.warning(f"⚠️ Файл сессии {SESSION_FILE} не найден.")
            return {}
    else:
        target_file = SESSION_FILE

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            state_data = json.load(f)
            cookies_list = state_data.get("cookies", []) if isinstance(state_data, dict) else []
            cookies = {c["name"]: c["value"] for c in cookies_list if isinstance(c, dict) and c.get("name") in ["mc-session", "mc-sid"]}
            
            if "mc-session" in cookies and "mc-sid" in cookies:
                return cookies
            else:
                logger.warning(f"⚠️ В {target_file} отсутствуют обязательные куки 'mc-session' или 'mc-sid'.")
    except Exception as e:
        logger.error(f"⚠️ Ошибка чтения файла кук {target_file}: {e}")

    return {}

def format_time(ts_ms) -> str:
    if not ts_ms:
        return "--:--"
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000).strftime('%d.%m %H:%M:%S')
    except Exception:
        return "--:--"

def check_logistics_question(text: str) -> str | None:
    """Проверяет текст на ключевые слова доставки и возвращает готовый шаблон."""
    if not text:
        return None
    clean_text = text.lower()
    if any(word in clean_text for word in LOGISTICS_TRIGGERS["kk"]["keywords"]):
        return LOGISTICS_TRIGGERS["kk"]["reply"]
    if any(word in clean_text for word in LOGISTICS_TRIGGERS["ru"]["keywords"]):
        return LOGISTICS_TRIGGERS["ru"]["reply"]
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
