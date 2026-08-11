import asyncio
import uuid
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from backend.db.database import get_db
from backend.db.models import User, Subscription, Store, UserStore, StoreTariffDB, StoreBalanceDB, StoreReferralDB, ReferralProfileDB
from backend.billing import catalog as billing_catalog
from backend.security.crypto import encrypt_data, decrypt_data
from backend.routers.auth_v1 import get_current_user
from backend.kaspi_wrapper import KaspiAPI
from backend.services.playwright_worker import worker, Command
from backend.state_helpers import _save_session, _save_mc_credentials, _refresh_mc_session, _load_mc_cookies

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stores", tags=["Multi-Store Engine"])


class ConnectFullStoreRequest(BaseModel):
    name: str
    api_token: str
    email: Optional[str] = None
    password: Optional[str] = None
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


@router.post("/connect-full-store", response_model=StoreResponse)
async def connect_full_store(
    req: ConnectFullStoreRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Единая форма подключения магазина Kaspi:
    - Проверка API-Токена (X-Auth-Token)
    - Вход по email+паролю (авто-извлечение mc-session / mc-sid куков)
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

    # 3. Если переданы email+пароль — выполняем вход и вытягиваем куки
    cookies_str = req.cookies_raw or ""
    email_clean = None
    password_plain = None
    if req.email and req.password:
        email_clean = req.email.strip()
        password_plain = req.password
        success, msg, login_cookies = await asyncio.to_thread(
            worker.send_cmd, Command.LOGIN, {"email": email_clean, "password": password_plain}
        )
        if not success:
            raise HTTPException(status_code=400, detail=f"Ошибка входа в Kaspi MC: {msg}")

        # Креды сохраняем НИЖЕ, когда известен store_id — либо существующего,
        # либо только что созданного магазина. Никакого сохранения в общий
        # session.json: иначе автообновление одного магазина будет читать
        # креды другого.

        # Куки приходят от worker.send_cmd напрямую (де-глобализация
        # Playwright — каждый логин стейтлесс, без общего state.json,
        # который при параллельном логине двух разных магазинов мог бы
        # достаться не тому стору).
        if login_cookies:
            parts = [f"{name}={value}" for name, value in login_cookies.items()]
            cookies_str = "; ".join(parts)

    # 4. Проверяем существование магазина в базе
    existing_stmt = select(Store).where(or_(Store.merchant_id == merchant_id, Store.name == name_clean))
    ex_res = await db.execute(existing_stmt)
    existing_store = ex_res.scalar_one_or_none()

    encrypted_cookies = encrypt_data(cookies_str) if cookies_str else None

    if existing_store:
        # Обновляем существующий магазин
        existing_store.name = name_clean
        if api_token_clean and not api_token_clean.startswith("PRESERVE"):
            existing_store.encrypted_api_key = encrypt_data(api_token_clean)
            
        if encrypted_cookies:
            existing_store.encrypted_cookies = encrypted_cookies
            existing_store.status = "ACTIVE"
        existing_store.last_sync_at = datetime.utcnow()

        # ОБЯЗАТЕЛЬНО проверяем и привязываем связь UserStore!
        us_stmt = select(UserStore).where(UserStore.user_id == user.id, UserStore.store_id == existing_store.id)
        us_res = await db.execute(us_stmt)
        if not us_res.scalar_one_or_none():
            db.add(UserStore(user_id=user.id, store_id=existing_store.id, role="OWNER"))

        # Подстраховка на случай стора, заведённого до миграции 027 без
        # backfill (не должно случаться в норме, но без этого он тихо
        # оставался бы без тарифа/баланса навсегда).
        tariff_row = await db.get(StoreTariffDB, existing_store.id)
        if not tariff_row:
            now = datetime.utcnow()
            db.add(StoreTariffDB(
                store_id=existing_store.id,
                plan_code=billing_catalog.DEFAULT_PLAN_CODE,
                credit_addon_code=billing_catalog.DEFAULT_CREDIT_ADDON,
                billing_status="ACTIVE",
                billing_anchor_day=now.day,
                period_start=now,
                period_end=now + timedelta(days=36500),
                next_charge_at=now + timedelta(days=36500),
                activated_at=now,
            ))
        balance_row = await db.get(StoreBalanceDB, existing_store.id)
        if not balance_row:
            db.add(StoreBalanceDB(store_id=existing_store.id, balance_minor=0))

        await db.commit()

        # Креды на автообновление сессии сохраняем ИСКЛЮЧИТЕЛЬНО в строку
        # этого магазина — никакого общего session.json (см. HANDOFF 3.1,
        # "no fallback": чужой store_id не должен пользоваться этой сессией).
        if email_clean:
            _save_mc_credentials(email_clean, password_plain, store_id=existing_store.id)

        active_token = decrypt_data(existing_store.encrypted_api_key) if existing_store.encrypted_api_key else api_token_clean
        _save_session({"token": active_token, "merchant_id": existing_store.merchant_id})

        return StoreResponse(
            id=existing_store.id,
            merchant_id=existing_store.merchant_id,
            name=existing_store.name,
            status=existing_store.status,
            role="OWNER",
            last_sync_at=existing_store.last_sync_at.isoformat() if existing_store.last_sync_at else None,
            created_at=existing_store.created_at.isoformat()
        )

    encrypted_api_key = encrypt_data(api_token_clean)

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

    # Тарификация (Слайс 7): новый магазин стартует на DEV (безлимит, без
    # списаний) — суперадмин назначит реальный тариф вручную через админку.
    # Без этой строки credits.py/engine.py тихо откатывались бы на дефолты
    # (лимит NONE-тира вместо безлимита DEV) — явная строка безопаснее.
    now = datetime.utcnow()
    db.add(StoreTariffDB(
        store_id=new_store.id,
        plan_code=billing_catalog.DEFAULT_PLAN_CODE,
        credit_addon_code=billing_catalog.DEFAULT_CREDIT_ADDON,
        billing_status="ACTIVE",
        billing_anchor_day=now.day,
        period_start=now,
        period_end=now + timedelta(days=36500),
        next_charge_at=now + timedelta(days=36500),
        activated_at=now,
    ))
    db.add(StoreBalanceDB(store_id=new_store.id, balance_minor=0))

    # Реферальная атрибуция (Слайс 7): если юзер регистрировался по коду —
    # привязываем магазин к рефереру прямо сейчас, окно старта с момента
    # подключения магазина (не с момента ухода с DEV — упрощение: реальный
    # потолок начисления в referrals.py всё равно ограничен % от того, что
    # магазин реально заплатил в окне, так что раннее окно не переплачивает).
    if user.pending_referral_code:
        profile_stmt = select(ReferralProfileDB).where(
            ReferralProfileDB.code == user.pending_referral_code,
            ReferralProfileDB.is_active == True,  # noqa: E712
        )
        profile_res = await db.execute(profile_stmt)
        profile = profile_res.scalar_one_or_none()
        if profile and profile.user_id != user.id:
            db.add(StoreReferralDB(
                store_id=new_store.id,
                referrer_user_id=profile.user_id,
                referral_code=user.pending_referral_code,
                source="code",
                window_start=now,
                window_end=now + timedelta(days=31 * profile.commission_months),
            ))
        user.pending_referral_code = None

    await db.commit()

    # Аналогично — креды привязываются к id только что созданного магазина.
    if email_clean:
        _save_mc_credentials(email_clean, password_plain, store_id=new_store.id)

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


@router.post("/{store_id}/verify", response_model=StoreResponse)
async def verify_store_cookies(
    store_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Инспекция живой сессии куков магазина (Health Check) с авто-синхронизацией"""
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

    def _check_cookies_valid(store_id: str) -> bool:
        # Раньше здесь был GET https://mc.shop.kaspi.kz/merchantcab/api/user/current
        # — этот эндпоинт у Kaspi отдаёт 404 "Not Found" ВСЕГДА, независимо
        # от валидности куков (проверено вживую: BFF-запросы теми же куками
        # работают, этот "health-check" — нет). Из-за этого ЛЮБОЙ магазин
        # с рабочими куками помечался COOKIE_EXPIRED. Заменено на тот же
        # реальный browser-based механизм, что уже проверенно работает в
        # /api/mc-status?verify=true (playwright_worker.Command.VERIFY_SESSION —
        # заходит в кабинет и смотрит, не редиректнуло ли на логин).
        mc_cookies = _load_mc_cookies(store_id=store_id)
        if not mc_cookies:
            return False
        success, is_valid, _fresh = worker.send_cmd(Command.VERIFY_SESSION, mc_cookies)
        return bool(success and is_valid)

    is_valid = await asyncio.to_thread(_check_cookies_valid, store_id)

    # Куки этого магазина просрочены — пробуем обновить сессию ЕГО СОБСТВЕННЫМИ
    # email/паролем. Никакого фоллбека на общую систему/другой магазин: если
    # своих кредов нет или вход не удался, пользователь должен войти заново.
    if not is_valid and await asyncio.to_thread(_refresh_mc_session, store_id=store_id):
        is_valid = await asyncio.to_thread(_check_cookies_valid, store_id)

    if is_valid:
        store.status = "ACTIVE"
        store.last_sync_at = datetime.utcnow()
    else:
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


@router.post("/{store_id}/sync-cookies", response_model=StoreResponse)
async def sync_store_cookies(
    store_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Принудительное обновление сессии Kaspi MC для конкретного магазина.

    Использует ТОЛЬКО email/пароль, сохранённые для этого store_id — без
    фоллбека на общий state.json/session.json или сессию другого магазина.
    Если своих кредов нет или вход не удался — нужен повторный вход.
    """
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

    if not await asyncio.to_thread(_refresh_mc_session, store_id=store_id):
        raise HTTPException(
            status_code=400,
            detail="Не удалось обновить сессию Kaspi MC: для этого магазина нет сохранённых email/пароля, "
                   "либо вход не удался. Войдите в Kaspi MC заново."
        )

    mc_cookies = _load_mc_cookies(store_id=store_id)
    if not mc_cookies:
        raise HTTPException(status_code=400, detail="Сессия обновлена, но куки не были сохранены. Войдите в Kaspi MC заново.")

    # Раньше здесь был GET .../merchantcab/api/user/current — этот эндпоинт
    # у Kaspi отдаёт 404 всегда, независимо от валидности куков (см. verify_store_cookies
    # выше). Заменено на тот же реальный browser-based VERIFY_SESSION.
    try:
        success, is_valid, _fresh = await asyncio.to_thread(worker.send_cmd, Command.VERIFY_SESSION, mc_cookies)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка проверки куков: {e}")

    if not (success and is_valid):
        store.status = "COOKIE_EXPIRED"
        await db.commit()
        raise HTTPException(status_code=400, detail="Куки Kaspi MC истекли сразу после обновления сессии. Войдите заново.")

    store.status = "ACTIVE"
    store.last_sync_at = datetime.utcnow()
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
