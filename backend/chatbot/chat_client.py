"""
Тонкие обёртки над Kaspi Chat API (mc.shop.kaspi.kz/chats/api/mobile/api/v1/).
Возвращают сырые объекты Kaspi как есть — без урезания под нужды AI-бота
(см. backend/chatbot/kaspi.py::get_chat_history, который делает то же самое,
но сплющивает сообщения в текстовые строки для промпта). Эти функции — для
собственного UI чата в дашборде (REAL_IMPORTANT/чаты_kaspi/KASPI_CHAT_API.md).

ВАЖНО: sender="client" в ответах Kaspi означает сообщение ОТ МАГАЗИНА,
sender="messenger" — от покупателя. Контринтуитивно, задокументировано и
подтверждено вживую — см. тот же .md.
"""
import time
import uuid
import asyncio
import logging

from backend.chatbot.kaspi import http_session

logger = logging.getLogger(__name__)

BASE_URL = "https://mc.shop.kaspi.kz/chats/api/mobile/api/v1"


def _headers(merchant_id: str) -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://msg-web.kaspi.kz",
        "Referer": "https://msg-web.kaspi.kz/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-app-id": "475C3442-B742-4EE7-B10B-6738A0830492",
        "x-auth-type": "Webchat",
        "x-locale": "ru-RU",
        "x-merchant-id": merchant_id,
        "x-platform-type": "WEB",
    }


async def diff_page(cookies: dict, merchant_id: str, gts: int, limit: int = 200) -> dict | None:
    """Одна страница getDiffGroups: диалоги, изменённые после gts, ОТ СТАРЫХ К
    НОВЫМ, плюс флаг more. None — Kaspi ответил не 200."""
    payload = {"counters": [], "limit": limit, "gts": gts}

    def fetch():
        return http_session.post(f"{BASE_URL}/group/getDiffGroups/chat", headers=_headers(merchant_id), cookies=cookies, json=payload, timeout=10)

    resp = await asyncio.to_thread(fetch)
    if resp.status_code != 200:
        logger.warning(f"[ChatClient] getDiffGroups вернул {resp.status_code}")
        return None
    return resp.json().get("data") or {}


# Окна, в которых ищем свежие диалоги для списка: сначала сутки, дальше шире,
# пока не наберём limit. Страниц на окно — с запасом под ~200 диалогов в сутки.
_RECENT_WINDOWS_DAYS = (1, 3, 7, 14)
_MAX_PAGES_PER_WINDOW = 10


async def list_groups(cookies: dict, merchant_id: str, since_days: int = 14, limit: int = 50) -> list[dict]:
    """Последние limit диалогов (новые сверху) за since_days дней.

    getDiffGroups отдаёт от старых к новым, поэтому запрос «с since_days назад,
    limit N» возвращал N САМЫХ СТАРЫХ диалогов окна, а не свежие. Листаем
    вперёд от узкого окна и берём хвост."""
    now_ms = int(time.time() * 1000)
    windows = [d for d in _RECENT_WINDOWS_DAYS if d < since_days] + [since_days]
    groups: dict = {}
    for days in windows:
        groups = {}
        gts = now_ms - days * 24 * 3600 * 1000
        for _ in range(_MAX_PAGES_PER_WINDOW):
            data = await diff_page(cookies, merchant_id, gts, limit=200)
            if data is None:
                break
            page = data.get("groups") or []
            for g in page:
                groups[g.get("id")] = g
            page_max = max((g.get("gts") or 0 for g in page), default=0)
            if not page or not data.get("more") or page_max <= gts:
                break
            gts = page_max
        if len(groups) >= limit:
            break
    ordered = sorted(groups.values(), key=lambda g: g.get("gts") or 0, reverse=True)
    return ordered[:limit]


async def get_history(group_id: str, cookies: dict, merchant_id: str, limit: int = 30) -> list[dict]:
    """Сырая история сообщений одного диалога, отсортированная от старых к новым."""
    payload = {"limit": limit, "groupId": group_id}

    def fetch():
        return http_session.post(f"{BASE_URL}/history/loadMoreMessages", headers=_headers(merchant_id), cookies=cookies, json=payload, timeout=10)

    resp = await asyncio.to_thread(fetch)
    if resp.status_code != 200:
        logger.warning(f"[ChatClient] loadMoreMessages вернул {resp.status_code} для {group_id}")
        return []
    messages = resp.json().get("data", {}).get("messages", [])
    messages.sort(key=lambda m: m.get("created", 0))
    return messages


async def send_message(group_id: str, text: str, cookies: dict, merchant_id: str) -> dict:
    """Отправляет текстовое сообщение. Возвращает {success, message?, error?}."""
    headers = _headers(merchant_id)

    def send():
        payload = {
            "created": int(time.time() * 1000),
            "data": {"text": text},
            "messageId": str(uuid.uuid4()).upper(),
            "groupId": group_id,
            "transitionContextUrl": f"https://pay.kaspi.kz/chat?threadId={group_id}&isWeb=true",
        }
        return http_session.post(f"{BASE_URL}/messages/sendMessage", headers=headers, cookies=cookies, json=payload, timeout=10)

    resp = await asyncio.to_thread(send)
    if resp.status_code != 200:
        return {"success": False, "error": f"HTTP {resp.status_code}"}
    data = resp.json()
    if data.get("StatusCode") not in (0, None):
        return {"success": False, "error": data}
    return {"success": True, "message": data.get("data")}


async def send_typing(group_id: str, text: str, cookies: dict, merchant_id: str) -> None:
    """Показывает клиенту индикатор набора текста (best-effort, ошибки не пробрасывает)."""
    def send():
        return http_session.post(f"{BASE_URL}/typing/sendText", headers=_headers(merchant_id), cookies=cookies, json={"groupId": group_id, "text": text}, timeout=5)

    try:
        await asyncio.to_thread(send)
    except Exception as e:
        logger.warning(f"[ChatClient] send_typing не удался для {group_id}: {e}")


async def mark_read(group_id: str, message_ids: list[dict], cookies: dict, merchant_id: str) -> bool:
    """message_ids: [{'id': ..., 'created': ...}, ...]"""
    def send():
        payload = {"groupId": group_id, "messages": message_ids}
        return http_session.post(f"{BASE_URL}/messageStatus/changeStatus", headers=_headers(merchant_id), cookies=cookies, json=payload, timeout=8)

    resp = await asyncio.to_thread(send)
    return resp.status_code == 200
