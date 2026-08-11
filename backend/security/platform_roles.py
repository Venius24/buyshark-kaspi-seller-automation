"""Платформенные роли — ортогональны UserStore.role (авторизация ВНУТРИ
конкретного магазина). Два независимых факта, намеренно не один enum:
User.is_superadmin (полный доступ к /api/admin/*) и наличие строки в
referral_profiles (наличие = "этот пользователь реферер"). Так снимается
вопрос "может ли суперадмин быть реферером" — оба факта существуют
независимо, обычный пользователь может быть и тем, и другим, и ни тем ни
другим сразу."""
from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import User, ReferralProfileDB
from backend.routers.auth_v1 import get_current_user


async def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Требуются права суперадмина")
    return user


async def require_referrer(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReferralProfileDB:
    """Суперадмин тоже проходит, но получает НЕограниченный по своему
    user_id профиль не создаётся автоматически — суперадмину нужен свой
    referral_profiles ряд, если он хочет видеть /api/referral/* как реферер.
    Обычным пользователям без профиля — 403."""
    result = await db.execute(select(ReferralProfileDB).where(ReferralProfileDB.user_id == user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Вы не зарегистрированы как реферер")
    return profile
