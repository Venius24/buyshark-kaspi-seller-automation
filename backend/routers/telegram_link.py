import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.db.models import User
from backend.routers.auth_v1 import get_current_user
from backend.security.store_context import _resolve_store_for_user
from backend.chatbot import telegram_identity as ti
from backend.chatbot import bot_instance
from backend.chatbot import notification_prefs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram Link"])

_bot_username_cache: Optional[str] = None


async def _get_bot_username() -> Optional[str]:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    if not bot_instance.bot:
        return None
    try:
        me = await bot_instance.bot.get_me()
        _bot_username_cache = me.username
        return _bot_username_cache
    except Exception as e:
        logger.error(f"Не удалось получить username бота: {e}")
        return None


async def _resolve_store_id(request: Request, db: AsyncSession, user_id: str) -> str:
    resolved = await _resolve_store_for_user(request, db, user_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="Магазин не найден. Сначала подключите магазин Kaspi.")
    return resolved[0].id


class InviteTokenRequest(BaseModel):
    role: str  # 'MANAGER' | 'VIEWER'
    max_uses: int = 1


class NotificationPrefsUpdate(BaseModel):
    chats: Optional[bool] = None
    problems: Optional[bool] = None
    money: Optional[bool] = None
    new_order_min_sum: Optional[int] = None
    quiet_from: Optional[str] = None
    quiet_to: Optional[str] = None


class TokenResponse(BaseModel):
    token: str
    deep_link: Optional[str]
    expires_in_minutes: int = ti.TOKEN_TTL_MINUTES


@router.post("/link-token", response_model=TokenResponse)
async def create_self_link_token(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Токен для владельца/уже существующего пользователя, чтобы привязать
    СВОЙ Telegram к выбранному магазину (X-Store-Id либо старейший)."""
    store_id = await _resolve_store_id(request, db, user.id)
    token = await asyncio.to_thread(
        ti.generate_link_token, store_id=store_id, created_by=user.id, self_user_id=user.id
    )
    if not token:
        raise HTTPException(status_code=500, detail="Не удалось создать токен привязки.")
    username = await _get_bot_username()
    deep_link = f"https://t.me/{username}?start={token}" if username else None
    return TokenResponse(token=token, deep_link=deep_link)


@router.post("/invite-token", response_model=TokenResponse)
async def create_invite_token(
    req: InviteTokenRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Токен-приглашение для сотрудника без веб-аккаунта. Только OWNER."""
    store_id = await _resolve_store_id(request, db, user.id)
    role = await asyncio.to_thread(ti.get_role, user.id, store_id)
    if role != "OWNER":
        raise HTTPException(status_code=403, detail="Приглашать сотрудников может только владелец магазина.")
    if req.role not in ti.EMPLOYEE_ROLES:
        raise HTTPException(status_code=400, detail=f"Роль должна быть одной из: {ti.EMPLOYEE_ROLES}")
    if req.max_uses < 1 or req.max_uses > 50:
        raise HTTPException(status_code=400, detail="max_uses должен быть от 1 до 50.")

    token = await asyncio.to_thread(
        ti.generate_link_token,
        store_id=store_id,
        created_by=user.id,
        invite_role=req.role,
        max_uses=req.max_uses,
    )
    if not token:
        raise HTTPException(status_code=500, detail="Не удалось создать токен приглашения.")
    username = await _get_bot_username()
    deep_link = f"https://t.me/{username}?start={token}" if username else None
    return TokenResponse(token=token, deep_link=deep_link)


@router.get("/members")
async def get_members(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Список Telegram-аккаунтов, привязанных к выбранному магазину — для
    экрана Настройки → Telegram в кабинете."""
    store_id = await _resolve_store_id(request, db, user.id)
    members = await asyncio.to_thread(ti.list_linked_members, store_id)
    role = await asyncio.to_thread(ti.get_role, user.id, store_id)
    return {"success": True, "store_id": store_id, "your_role": role, "members": members}


@router.delete("/members/{tg_user_id}")
async def remove_member(
    tg_user_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    store_id = await _resolve_store_id(request, db, user.id)
    ok = await asyncio.to_thread(ti.unlink_member, store_id, tg_user_id, user.id)
    if not ok:
        raise HTTPException(status_code=403, detail="Нет прав отвязать этого сотрудника, либо он не найден.")
    return {"success": True}


@router.get("/notification-prefs")
async def get_notification_prefs(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Настройки уведомлений — то же самое, что бот показывает в /notif,
    зеркально доступно из кабинета (единая таблица TelegramNotificationPrefDB)."""
    store_id = await _resolve_store_id(request, db, user.id)
    tg_user_id = await asyncio.to_thread(ti.get_tg_user_id_for_user, user.id)
    if not tg_user_id:
        return {"linked": False, "store_id": store_id}
    prefs = await asyncio.to_thread(notification_prefs.get_prefs, tg_user_id, store_id)
    return {"linked": True, "store_id": store_id, **prefs}


@router.put("/notification-prefs")
async def update_notification_prefs(
    req: NotificationPrefsUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    store_id = await _resolve_store_id(request, db, user.id)
    tg_user_id = await asyncio.to_thread(ti.get_tg_user_id_for_user, user.id)
    if not tg_user_id:
        raise HTTPException(status_code=400, detail="Сначала привяжите свой Telegram к этому магазину.")
    updates = req.model_dump(exclude_unset=True)
    prefs = await asyncio.to_thread(notification_prefs.update_prefs, tg_user_id, store_id, updates)
    return {"success": True, "store_id": store_id, **prefs}
