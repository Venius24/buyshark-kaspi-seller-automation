import os
import time
import uuid
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Response, Request, Cookie
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from backend.db.database import get_db
from backend.db.models import User, Subscription, UserSession, ReferralProfileDB
from backend.services import mailer
from backend.security.auth import (
    SECRET_KEY,
    ALGORITHM,
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    is_token_revoked,
    REFRESH_TOKEN_EXPIRE_DAYS
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth Engine"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    referral_code: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


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

    if is_token_revoked(payload, user.sessions_revoked_at):
        raise HTTPException(status_code=401, detail="Сессия завершена после смены пароля. Войдите заново.")

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

    # Реф-код проверяем ДО создания юзера, чтобы невалидный/чужой код не
    # тихо проглатывался — просто не сохраняем его, атрибуции не будет.
    pending_referral_code = None
    if req.referral_code:
        code_clean = req.referral_code.strip().upper()
        ref_stmt = select(ReferralProfileDB).where(ReferralProfileDB.code == code_clean, ReferralProfileDB.is_active == True)  # noqa: E712
        ref_res = await db.execute(ref_stmt)
        if ref_res.scalar_one_or_none():
            pending_referral_code = code_clean

    # Создаем пользователя
    new_user = User(
        email=req.email.lower(),
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        phone=req.phone,
        is_active=True,
        is_verified=False,
        pending_referral_code=pending_referral_code,
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

    # Сотрудники, заведённые через Telegram-приглашение (is_telegram_only),
    # не имеют реального пароля (password_hash="") — verify_password упала бы
    # на невалидном формате хеша вместо честного 401/403, если бы кто-то
    # угадал их синтетический email tg<id>@telegram.local.
    if user and user.is_telegram_only:
        raise HTTPException(status_code=403, detail="Этот аккаунт создан через Telegram и не имеет входа в веб-кабинет.")

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
            "phone": user.phone,
            # Сотрудник вошёл с временным паролем, который владелец видел
            # своими глазами (см. routers/employees.py) — кабинет обязан
            # показать экран смены пароля вместо рабочих вкладок.
            "must_change_password": user.must_change_password,
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

    if is_token_revoked(payload, user.sessions_revoked_at):
        raise HTTPException(status_code=401, detail="Сессия завершена после смены пароля. Войдите заново.")

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

    referrer_stmt = select(ReferralProfileDB.user_id).where(ReferralProfileDB.user_id == user.id)
    referrer_res = await db.execute(referrer_stmt)
    is_referrer = referrer_res.scalar_one_or_none() is not None

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "phone": user.phone,
            "is_active": user.is_active,
            "is_verified": user.is_verified,
            "is_superadmin": user.is_superadmin,
            "is_referrer": is_referrer,
            "must_change_password": user.must_change_password,
            "onboarding_completed": user.onboarding_completed_at is not None,
        },
        "subscription": {
            "plan_type": sub.plan_type if sub else "STARTER",
            "max_stores": sub.max_stores if sub else 1,
            "max_skus_in_auto_price": sub.max_skus_in_auto_price if sub else 100,
            "is_api_enabled": sub.is_api_enabled if sub else False,
            "valid_until": sub.valid_until.isoformat() if sub else None
        }
    }


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Смена собственного пароля. Для сотрудника с временным паролем это
    единственное действие, доступное до снятия must_change_password."""
    if user.is_telegram_only:
        raise HTTPException(status_code=403, detail="У Telegram-аккаунта нет пароля от веб-кабинета")
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Текущий пароль неверен")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Новый пароль должен быть не короче 8 символов")
    if verify_password(req.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Новый пароль совпадает со старым")

    user.password_hash = hash_password(req.new_password)
    user.must_change_password = False
    await db.commit()
    logger.info("Пользователь %s сменил пароль", user.email)
    return {"success": True}


@router.post("/onboarding-complete")
async def complete_onboarding(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Отмечает онбординг-тур пройденным (или явно пропущенным) для юзера."""
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = datetime.utcnow()
        await db.commit()
    return {"onboarding_completed": True}


# --- Восстановление пароля по email ---------------------------------------
#
# Токен сброса — JWT без отдельной таблицы: в нём отпечаток текущего
# password_hash, поэтому после смены пароля (любым путём) все ранее выданные
# ссылки гаснут сами — одноразовость без хранения состояния.

PASSWORD_RESET_EXPIRE_MINUTES = 60
_FORGOT_EMAIL_COOLDOWN_SEC = 60
_FORGOT_IP_LIMIT = 10
_FORGOT_IP_WINDOW_SEC = 3600
_forgot_last_by_email: dict[str, float] = {}
_forgot_hits_by_ip: dict[str, list[float]] = {}


def _password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def create_password_reset_token(user: User) -> str:
    return jwt.encode(
        {
            "sub": user.id,
            "type": "pwd_reset",
            "pf": _password_fingerprint(user.password_hash),
            "exp": datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def _forgot_rate_limited(email: str, ip: str) -> bool:
    now = time.monotonic()
    last = _forgot_last_by_email.get(email)
    if last is not None and now - last < _FORGOT_EMAIL_COOLDOWN_SEC:
        return True
    hits = [t for t in _forgot_hits_by_ip.get(ip, []) if now - t < _FORGOT_IP_WINDOW_SEC]
    if len(hits) >= _FORGOT_IP_LIMIT:
        _forgot_hits_by_ip[ip] = hits
        return True
    hits.append(now)
    _forgot_hits_by_ip[ip] = hits
    _forgot_last_by_email[email] = now
    return False


def _reset_url(token: str) -> str:
    base = os.getenv("APP_PUBLIC_URL", "https://app.buyshark.kz").rstrip("/")
    return f"{base}/?reset_token={token}"


@router.post("/forgot-password")
async def forgot_password(
    req: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Шлёт ссылку сброса пароля. Ответ всегда одинаковый — по нему нельзя
    узнать, зарегистрирован ли email; письмо уходит в фоне, чтобы и время
    ответа этого не выдавало."""
    email = req.email.lower()
    ip = request.client.host if request.client else "?"
    if _forgot_rate_limited(email, ip):
        logger.info("forgot-password: лимит для %s / %s", email, ip)
        return {"success": True}

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user and user.is_active and not user.is_telegram_only:
        subject, html, text = mailer.render_password_reset(_reset_url(create_password_reset_token(user)))
        background_tasks.add_task(mailer.send_email, user.email, subject, html, text)
        logger.info("forgot-password: ссылка сброса поставлена в отправку для %s", email)
    else:
        logger.info("forgot-password: запрос для неизвестного/неактивного %s", email)
    return {"success": True}


@router.post("/reset-password")
async def reset_password(
    req: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Задаёт новый пароль по токену из письма."""
    invalid = HTTPException(status_code=400, detail="Ссылка недействительна или устарела. Запросите восстановление ещё раз.")
    try:
        payload = jwt.decode(req.token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise invalid
    if payload.get("type") != "pwd_reset" or not payload.get("sub"):
        raise invalid

    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user or not user.is_active or user.is_telegram_only:
        raise invalid
    if payload.get("pf") != _password_fingerprint(user.password_hash):
        raise invalid  # пароль уже меняли после выдачи ссылки — ссылка одноразовая

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Новый пароль должен быть не короче 8 символов")

    user.password_hash = hash_password(req.new_password)
    user.must_change_password = False
    # Человек доказал владение ящиком — email подтверждён.
    user.is_verified = True
    # Если пароль сбрасывают из-за угона — выкидываем все уже открытые
    # сессии: токены, выданные до этой отметки, больше не принимаются.
    user.sessions_revoked_at = datetime.utcnow()
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.commit()
    logger.info("Пользователь %s сбросил пароль по ссылке из письма", user.email)
    return {"success": True}
