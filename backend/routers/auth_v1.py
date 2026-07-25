import os
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Cookie
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.db.database import get_db
from backend.db.models import User, Subscription, UserSession
from backend.security.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    REFRESH_TOKEN_EXPIRE_DAYS
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth Engine"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    phone: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict
    subscription: dict


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """Dependency for extracting and validating authenticated JWT User"""
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Необходима авторизация (Токен отсутствует)"
        )

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или истекший AccessToken"
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Неверная нагрузка токена")

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь не найден или заблокирован")

    return user


@router.post("/register", response_model=AuthResponse)
async def register_user(
    req: RegisterRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Регистрация нового пользователя SaaS с созданием бесплатной подписки STARTER"""
    stmt = select(User).where(User.email == req.email.lower())
    res = await db.execute(stmt)
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Пользователь с таким Email уже зарегистрирован")

    # Создаем пользователя
    new_user = User(
        email=req.email.lower(),
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        phone=req.phone,
        is_active=True,
        is_verified=False
    )
    db.add(new_user)
    await db.flush()

    # Создаем подписку по умолчанию
    new_sub = Subscription(
        user_id=new_user.id,
        plan_type="STARTER",
        max_stores=1,
        max_skus_in_auto_price=100,
        is_api_enabled=False,
        valid_until=datetime.utcnow() + timedelta(days=30)
    )
    db.add(new_sub)

    # Создаем токена
    token_data = {"sub": new_user.id, "email": new_user.email}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # Записываем сессию
    session_item = UserSession(
        user_id=new_user.id,
        refresh_token_hash=hash_password(refresh_token),
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.client.host if request.client else None,
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    db.add(session_item)
    await db.commit()

    # Устанавливаем HttpOnly Cookie для RefreshToken
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    )

    return AuthResponse(
        access_token=access_token,
        user={
            "id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "phone": new_user.phone
        },
        subscription={
            "plan_type": new_sub.plan_type,
            "max_stores": new_sub.max_stores,
            "max_skus_in_auto_price": new_sub.max_skus_in_auto_price,
            "valid_until": new_sub.valid_until.isoformat()
        }
    )


@router.post("/login", response_model=AuthResponse)
async def login_user(
    req: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Аутентификация по Email/Паролю"""
    stmt = select(User).where(User.email == req.email.lower())
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Неверный Email или пароль")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Ваша учетная запись заблокирована")

    # Извлекаем подписку
    sub_stmt = select(Subscription).where(Subscription.user_id == user.id)
    sub_res = await db.execute(sub_stmt)
    sub = sub_res.scalar_one_or_none()

    token_data = {"sub": user.id, "email": user.email}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    session_item = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_password(refresh_token),
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.client.host if request.client else None,
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    db.add(session_item)
    await db.commit()

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    )

    return AuthResponse(
        access_token=access_token,
        user={
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "phone": user.phone
        },
        subscription={
            "plan_type": sub.plan_type if sub else "STARTER",
            "max_stores": sub.max_stores if sub else 1,
            "max_skus_in_auto_price": sub.max_skus_in_auto_price if sub else 100,
            "valid_until": sub.valid_until.isoformat() if sub else datetime.utcnow().isoformat()
        }
    )


@router.post("/refresh")
async def refresh_tokens(
    request: Request,
    response: Response,
    refresh_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """Обновление AccessToken по RefreshToken из HttpOnly Cookie"""
    token_str = refresh_token or request.headers.get("X-Refresh-Token")
    if not token_str:
        raise HTTPException(status_code=401, detail="RefreshToken отсутствует")

    payload = decode_token(token_str)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Недействительный RefreshToken")

    user_id = payload.get("sub")
    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    new_access_token = create_access_token({"sub": user.id, "email": user.email})
    return {"access_token": new_access_token, "token_type": "bearer"}


@router.get("/me")
async def get_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Получение профиля текущего пользователя и квот подписки"""
    sub_stmt = select(Subscription).where(Subscription.user_id == user.id)
    sub_res = await db.execute(sub_stmt)
    sub = sub_res.scalar_one_or_none()

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "phone": user.phone,
            "is_active": user.is_active,
            "is_verified": user.is_verified
        },
        "subscription": {
            "plan_type": sub.plan_type if sub else "STARTER",
            "max_stores": sub.max_stores if sub else 1,
            "max_skus_in_auto_price": sub.max_skus_in_auto_price if sub else 100,
            "is_api_enabled": sub.is_api_enabled if sub else False,
            "valid_until": sub.valid_until.isoformat() if sub else None
        }
    }
