"""
Роутер "Сделайте всё за меня" — авто-подключение магазина через
владельческий SMS-вход. См. AUTOCONNECT_PLAN.md и
backend/services/kaspi_auto_connect.py.

Выключено по умолчанию (AUTO_CONNECT_ENABLED=1 в .env, чтобы включить) —
Фаза 1 (реальные селекторы кабинета "Сотрудники"/"Интеграция") ещё не
сделана, отдаём понятный 503 вместо того, чтобы вешать пользователя на
автоматизацию, которая гарантированно упадёт на середине.
"""

import os
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.db.models import User
from backend.routers.auth_v1 import get_current_user
from backend.services import kaspi_auto_connect as auto

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stores/auto-connect", tags=["Auto-Connect (владелец)"])


def _auto_connect_enabled() -> bool:
    return os.getenv("AUTO_CONNECT_ENABLED", "0") == "1"


class StartAutoConnectRequest(BaseModel):
    phone: str


class VerifyAutoConnectRequest(BaseModel):
    phone: str
    code: str
    store_name: str


def _guard_enabled():
    if not _auto_connect_enabled():
        raise HTTPException(
            status_code=503,
            detail="Автоподключение «Сделайте всё за меня» пока в разработке — используйте ручное подключение.",
        )


@router.post("/start")
async def start_auto_connect(
    req: StartAutoConnectRequest,
    user: User = Depends(get_current_user),
):
    """Шаг 1 — отправить SMS-код на телефон владельца кабинета Kaspi MC."""
    _guard_enabled()
    # Магазина в БД ещё нет — ключ незавершённой SMS-попытки привязываем
    # к пользователю, а не к store_id (см. kaspi_auto_connect.py).
    session_key = f"autoconnect:{user.id}"
    success, msg, _ = auto.start_owner_sms_login(session_key, req.phone)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "sms_sent"}


@router.post("/verify")
async def verify_auto_connect(
    req: VerifyAutoConnectRequest,
    user: User = Depends(get_current_user),
):
    """Шаг 2 — код введён: владельческий вход подтверждён, дальше —
    создание сотрудника + получение токена (Фаза 1, ещё не реализовано)."""
    _guard_enabled()
    session_key = f"autoconnect:{user.id}"
    success, msg, owner_cookies = auto.submit_owner_sms_code(session_key, req.code)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    # Фаза 1 не реализована — кидаем понятную 501, а не тихо создаём
    # магазин без API-токена/сотрудника.
    try:
        result = auto.create_employee_and_fetch_token(owner_cookies, req.store_name)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))

    return result
