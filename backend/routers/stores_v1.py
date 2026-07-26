import json
import os
import uuid
import hashlib
import logging
import requests
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from backend.db.database import get_db
from backend.db.models import User, Subscription, Store, UserStore
from backend.security.crypto import encrypt_data, decrypt_data
from backend.routers.auth_v1 import get_current_user
from backend.kaspi_wrapper import KaspiAPI
from backend.services.playwright_worker import worker, Command
from backend.state_helpers import _save_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stores", tags=["Multi-Store Engine"])


class StartSmsRequest(BaseModel):
    phone: str


class ConnectFullStoreRequest(BaseModel):
    name: str
    api_token: str
    phone: Optional[str] = None
    sms: Optional[str] = None
    cookies_raw: Optional[str] = None


class StoreResponse(BaseModel):
    id: str
    merchant_id: str
    name: str
    status: str
    role: str
    last_sync_at: Optional[str]
    created_at: str


async def check_quota_guard(user_id: str, db: AsyncSession):
    """Проверка лимитов на количество доступных магазинов в подписке"""
    sub_stmt = select(Subscription).where(Subscription.user_id == user_id)
    sub_res = await db.execute(sub_stmt)
    sub = sub_res.scalar_one_or_none()
    max_stores = sub.max_stores if sub else 1

    count_stmt = (
        select(func.count(UserStore.store_id))
        .where(UserStore.user_id == user_id, UserStore.role == "OWNER")
    )
    count_res = await db.execute(count_stmt)
    owned_count = count_res.scalar() or 0

    if owned_count >= max_stores:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Достигнут лимит магазинов для вашего тарифа ({owned_count}/{max_stores}). Обновите тариф!"
        )


@router.get("", response_model=List[StoreResponse])
async def list_user_stores(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Получение списка магазинов Kaspi, доступных текущему пользователю"""
    stmt = (
        select(Store, UserStore.role)
        .join(UserStore, Store.id == UserStore.store_id)
        .where(UserStore.user_id == user.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    stores_list = []
    for store, role in rows:
        stores_list.append(
            StoreResponse(
                id=store.id,
                merchant_id=store.merchant_id,
                name=store.name,
                status=store.status,
                role=role,
                last_sync_at=store.last_sync_at.isoformat() if store.last_sync_at else None,
                created_at=store.created_at.isoformat()
            )
        )

    return stores_list


@router.post("", response_model=StoreResponse)
async def create_store(
    req: ConnectFullStoreRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Прямое или стандартное создание магазина Kaspi"""
    return await connect_full_store(req, user, db)


@router.post("/start-sms-login")
async def start_sms_login(
    req: StartSmsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Шаг 1: Запрос SMS-кода авторизации в Kaspi Merchant"""
    await check_quota_guard(user.id, db)

    phone = req.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Не передан номер телефона")

    success, msg = worker.send_cmd(Command.START_LOGIN, phone)
    if success:
        return {"success": True, "message": "SMS-код успешно отправлен на ваш номер телефона"}
    else:
        raise HTTPException(status_code=400, detail=msg)


@router.post("/connect-full-store", response_model=StoreResponse)
async def connect_full_store(
    req: ConnectFullStoreRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Единая форма подключения магазина Kaspi:
    - Проверка API-Токена (X-Auth-Token)
    - Вход по SMS (авто-извлечение mc-session / mc-sid куков)
    - Encryption at Rest (шифрование AES-256)
    - Автоматическая привязка UserStore к текущему аккаунту
    """
    await check_quota_guard(user.id, db)

    name_clean = req.name.strip()
    api_token_clean = req.api_token.strip()

    if not name_clean:
        raise HTTPException(status_code=400, detail="Название магазина не может быть пустым")

    if not api_token_clean:
        raise HTTPException(status_code=400, detail="API токен (X-Auth-Token) обязателен для подключения")

    # 1. Верификация API Токена через Kaspi Public API
    try:
        valid = KaspiAPI.verify_token(api_token_clean)
        if not valid:
            raise HTTPException(status_code=400, detail="Kaspi API отклонил ваш токен (X-Auth-Token). Проверьте правильность.")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Ошибка проверки API токена: {e}")

    # 2. Определение Merchant ID без хардкода
    merchant_id = None
    try:
        merchant_id = KaspiAPI.get_merchant_id(api_token_clean)
    except Exception as e:
        logger.warning(f"Определение merchant_id из токена: {e}")

    if not merchant_id or merchant_id.startswith("m_20186986"):
        token_hash = hashlib.md5(api_token_clean.encode('utf-8')).hexdigest()[:8]
        merchant_id = f"m_{token_hash}"

    # 3. Если передан SMS-код — завершаем вход и вытягиваем куки
    cookies_str = req.cookies_raw or ""
    if req.sms and req.sms.strip():
        sms_clean = req.sms.strip()
        success, msg = worker.send_cmd(Command.SUBMIT_SMS, sms_clean)
        if not success:
            raise HTTPException(status_code=400, detail=f"Ошибка входа по SMS: {msg}")

        if os.path.exists("state.json"):
            try:
                with open("state.json", "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                    cookie_list = state_data.get("cookies", [])
                    parts = [f"{c['name']}={c['value']}" for c in cookie_list]
                    cookies_str = "; ".join(parts)
            except Exception as e:
                logger.error(f"Ошибка чтения state.json: {e}")

    # 4. Проверяем существование магазина в базе
    existing_stmt = select(Store).where(Store.merchant_id == merchant_id)
    ex_res = await db.execute(existing_stmt)
    existing_store = ex_res.scalar_one_or_none()

    encrypted_api_key = encrypt_data(api_token_clean)
    encrypted_cookies = encrypt_data(cookies_str) if cookies_str else None

    if existing_store:
        # Обновляем существующий магазин
        existing_store.name = name_clean
        existing_store.encrypted_api_key = encrypted_api_key
        if encrypted_cookies:
            existing_store.encrypted_cookies = encrypted_cookies
            existing_store.status = "ACTIVE"
        existing_store.last_sync_at = datetime.utcnow()

        # ОБЯЗАТЕЛЬНО проверяем и привязываем связь UserStore!
        us_stmt = select(UserStore).where(UserStore.user_id == user.id, UserStore.store_id == existing_store.id)
        us_res = await db.execute(us_stmt)
        if not us_res.scalar_one_or_none():
            db.add(UserStore(user_id=user.id, store_id=existing_store.id, role="OWNER"))

        await db.commit()

        # Сохраняем сессию активного токена и merchant_id для REST подсистем
        _save_session({"token": api_token_clean, "merchant_id": existing_store.merchant_id})

        return StoreResponse(
            id=existing_store.id,
            merchant_id=existing_store.merchant_id,
            name=existing_store.name,
            status=existing_store.status,
            role="OWNER",
            last_sync_at=existing_store.last_sync_at.isoformat() if existing_store.last_sync_at else None,
            created_at=existing_store.created_at.isoformat()
        )

    # 5. Создаем новый магазин
    new_store = Store(
        merchant_id=merchant_id,
        name=name_clean,
        encrypted_api_key=encrypted_api_key,
        encrypted_cookies=encrypted_cookies,
        status="ACTIVE" if (cookies_str or api_token_clean) else "PAUSED",
        last_sync_at=datetime.utcnow()
    )
    db.add(new_store)
    await db.flush()

    user_store = UserStore(
        user_id=user.id,
        store_id=new_store.id,
        role="OWNER"
    )
    db.add(user_store)
    await db.commit()

    # Сохраняем сессию активного токена и merchant_id для REST подсистем
    _save_session({"token": api_token_clean, "merchant_id": new_store.merchant_id})

    return StoreResponse(
        id=new_store.id,
        merchant_id=new_store.merchant_id,
        name=new_store.name,
        status=new_store.status,
        role="OWNER",
        last_sync_at=new_store.last_sync_at.isoformat() if new_store.last_sync_at else None,
        created_at=new_store.created_at.isoformat()
    )


@router.post("/{store_id}/select")
async def select_active_store(
    store_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Активация контекста выбранного магазина для всех подсистем"""
    stmt = (
        select(Store)
        .join(UserStore, Store.id == UserStore.store_id)
        .where(Store.id == store_id, UserStore.user_id == user.id)
    )
    res = await db.execute(stmt)
    store = res.scalar_one_or_none()

    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден или нет прав доступа")

    decrypted_api_key = decrypt_data(store.encrypted_api_key)
    if decrypted_api_key:
        _save_session({"token": decrypted_api_key, "merchant_id": store.merchant_id})

    if store.encrypted_cookies:
        decrypted_cookies = decrypt_data(store.encrypted_cookies)
        if decrypted_cookies:
            os.makedirs("states", exist_ok=True)
            cookies_pairs = [c.split("=", 1) for c in decrypted_cookies.split("; ") if "=" in c]
            cookie_objs = [{"name": k.strip(), "value": v.strip()} for k, v in cookies_pairs]
            with open("state.json", "w", encoding="utf-8") as f:
                json.dump({"cookies": cookie_objs}, f, indent=2)

    return {"status": "ok", "message": f"Контекст активного магазина {store.name} установлен."}


@router.post("/{store_id}/verify", response_model=StoreResponse)
async def verify_store_cookies(
    store_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Инспекция живой сессии куков магазина (Health Check)"""
    stmt = (
        select(Store, UserStore.role)
        .join(UserStore, Store.id == UserStore.store_id)
        .where(Store.id == store_id, UserStore.user_id == user.id)
    )
    res = await db.execute(stmt)
    row = res.first()

    if not row:
        raise HTTPException(status_code=404, detail="Магазин не найден или нет прав доступа")

    store, role = row
    decrypted_cookies = decrypt_data(store.encrypted_cookies)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": decrypted_cookies
    }

    try:
        r = requests.get("https://mc.shop.kaspi.kz/merchantcab/api/user/current", headers=headers, timeout=5)
        if r.status_code == 200:
            store.status = "ACTIVE"
            store.last_sync_at = datetime.utcnow()
        else:
            store.status = "COOKIE_EXPIRED"
    except Exception:
        store.status = "COOKIE_EXPIRED"

    await db.commit()

    return StoreResponse(
        id=store.id,
        merchant_id=store.merchant_id,
        name=store.name,
        status=store.status,
        role=role,
        last_sync_at=store.last_sync_at.isoformat() if store.last_sync_at else None,
        created_at=store.created_at.isoformat()
    )


@router.delete("/{store_id}")
async def delete_store(
    store_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Удаление магазина из системы"""
    stmt = select(UserStore).where(UserStore.store_id == store_id, UserStore.user_id == user.id, UserStore.role == "OWNER")
    res = await db.execute(stmt)
    user_store = res.scalar_one_or_none()

    if not user_store:
        raise HTTPException(status_code=403, detail="Только владелец магазина (OWNER) может его удалить")

    store_stmt = select(Store).where(Store.id == store_id)
    store_res = await db.execute(store_stmt)
    store = store_res.scalar_one_or_none()

    if store:
        await db.delete(store)
        await db.commit()

    return {"status": "ok", "message": "Магазин успешно удален"}
