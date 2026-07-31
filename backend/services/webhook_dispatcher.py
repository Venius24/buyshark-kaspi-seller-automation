import hmac
import hashlib
import json
import time
import asyncio
import logging
import requests
import datetime
from typing import Dict, Any, Optional

from backend.state_helpers import _load_webhook_config

logger = logging.getLogger(__name__)

async def dispatch_webhook_event(event_type: str, payload_data: Dict[str, Any], store_id: Optional[str] = None):
    """
    Отправляет HTTP Webhook POST запрос во внешнюю систему 1С/ERP этого магазина.
    Добавляет HMAC-SHA256 подпись в заголовок X-Webhook-Signature и выполняет 3 повторные попытки при ошибках.

    store_id given (Слайс 3): конфиг вебхука этого магазина (WebhookConfigDB).
    store_id=None (легаси, ещё не мигрированные вызовы): старый общий файл.
    """
    cfg = _load_webhook_config(store_id=store_id)
    if not cfg or not cfg.get("enabled", True):
        return

    url = cfg.get("url", "").strip()
    if not url:
        return

    secret = cfg.get("secret", "").strip()

    body = {
        "event": event_type,
        "timestamp": datetime.datetime.now().isoformat(),
        "data": payload_data
    }
    json_bytes = json.dumps(body, ensure_ascii=False).encode('utf-8')

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "KaspiSaaS-WebhookDispatcher/1.0"
    }

    if secret:
        signature = hmac.new(secret.encode('utf-8'), json_bytes, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = signature

    delays = [1, 5, 15]

    for attempt in range(len(delays)):
        try:
            resp = await asyncio.to_thread(
                requests.post,
                url,
                data=json_bytes,
                headers=headers,
                timeout=10
            )

            if 200 <= resp.status_code < 300:
                logger.info(f"✅ [Webhook] Событие '{event_type}' успешно доставлено на {url} (Status: {resp.status_code})")
                return
            else:
                logger.warning(f"⚠️ [Webhook] Доставка события '{event_type}' вернула код {resp.status_code} (Попытка {attempt+1}/3)")
        except Exception as e:
            logger.warning(f"⚠️ [Webhook] Ошибка доставки события '{event_type}': {e} (Попытка {attempt+1}/3)")

        if attempt < len(delays) - 1:
            await asyncio.sleep(delays[attempt])

    logger.error(f"❌ [Webhook] Не удалось доставить событие '{event_type}' на {url} после 3 попыток.")
