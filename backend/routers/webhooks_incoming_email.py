"""Приём писем от Cloudflare Email Worker, слушающего *@buyshark.kz (Email
Routing). Основной сценарий — приветственные письма Kaspi MC с временными
паролями для сотрудников, которых сервис создаёт на себя в
"Сделайте всё за меня" (см. AUTOCONNECT_PLAN.md,
backend/services/kaspi_auto_connect.py:generate_employee_credentials).

Воркер шлёт сырое MIME-письмо целиком (from/to/raw) — эндпоинт только
сохраняет его (зашифрованным) и парсит subject для отображения; извлечение
пароля/сопоставление с конкретной auto-connect сессией — отдельная задача,
когда Фаза 1 того флоу будет реализована.
"""
import os
import logging
from email import message_from_string
from email.header import decode_header

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.db.models import IncomingEmail
from backend.security.crypto import encrypt_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _decode_subject(raw_subject: str | None) -> str | None:
    if not raw_subject:
        return None
    parts = decode_header(raw_subject)
    decoded = ""
    for text, encoding in parts:
        if isinstance(text, bytes):
            decoded += text.decode(encoding or "utf-8", errors="replace")
        else:
            decoded += text
    return decoded


@router.post("/incoming-email")
async def receive_incoming_email(
    payload: dict,
    x_webhook_secret: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    expected_secret = os.getenv("INCOMING_EMAIL_WEBHOOK_SECRET")
    if not expected_secret or x_webhook_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    from_address = (payload.get("from") or "").strip()
    to_address = (payload.get("to") or "").strip()
    raw = payload.get("raw") or ""
    if not to_address or not raw:
        raise HTTPException(status_code=400, detail="Missing 'to' or 'raw'")

    subject = None
    try:
        parsed = message_from_string(raw)
        subject = _decode_subject(parsed.get("Subject"))
    except Exception:
        logger.exception("Не удалось распарсить subject входящего письма")

    record = IncomingEmail(
        to_address=to_address,
        from_address=from_address,
        subject=subject,
        encrypted_raw=encrypt_data(raw),
    )
    db.add(record)
    await db.flush()

    logger.info(f"📧 Входящее письмо: to={to_address} from={from_address} subject={subject!r}")
    return {"success": True, "id": record.id}
