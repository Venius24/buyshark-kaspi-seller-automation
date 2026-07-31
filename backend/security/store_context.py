import logging
from dataclasses import dataclass
from typing import Optional
from fastapi import Request, WebSocket, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import User, Store, UserStore
from backend.security.auth import decode_token
from backend.security.crypto import decrypt_data

logger = logging.getLogger(__name__)


@dataclass
class StoreContext:
    token: str
    merchant_id: str
    store_id: str


async def _resolve_store_for_user(request: Request, db: AsyncSession, user_id: str) -> Optional[Store]:
    store_id_header = request.headers.get("X-Store-Id")
    if store_id_header:
        stmt = (
            select(Store)
            .join(UserStore, Store.id == UserStore.store_id)
            .where(Store.id == store_id_header, UserStore.user_id == user_id)
        )
    else:
        # No explicit store selected — pick the user's oldest (first-connected) store.
        # Deterministic and matches today's de-facto single-store-per-user reality;
        # a user managing multiple stores concurrently must send X-Store-Id.
        stmt = (
            select(Store)
            .join(UserStore, Store.id == UserStore.store_id)
            .where(UserStore.user_id == user_id)
            .order_by(Store.created_at.asc())
        )
    res = await db.execute(stmt)
    return res.scalars().first()


async def get_store_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StoreContext:
    """
    Резолвит (token, merchant_id, store_id) для store-scoped эндпоинтов.

    Слайс 7: legacy-фоллбек на общую сессию (states/session.json) удалён
    целиком — запрос без валидного JWT Bearer, без активного User или без
    доступного этому пользователю Store (X-Store-Id либо единственный/
    старейший магазин) получает явный 401, а не тихий доступ к общей/чужой
    сессии. Раньше это было переходным поведением на время dual-auth
    миграции (Phase 4b-4e) — миграция завершена, все клиенты шлют JWT.
    """
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token_str = auth_header.split(" ", 1)[1]
        payload = decode_token(token_str)
        if payload and payload.get("type") == "access":
            user_id = payload.get("sub")
            if user_id:
                stmt = select(User).where(User.id == user_id)
                res = await db.execute(stmt)
                user = res.scalar_one_or_none()
                if user and user.is_active:
                    store = await _resolve_store_for_user(request, db, user.id)
                    if store and store.encrypted_api_key:
                        decrypted_token = decrypt_data(store.encrypted_api_key)
                        if decrypted_token:
                            return StoreContext(
                                token=decrypted_token,
                                merchant_id=store.merchant_id,
                                store_id=store.id,
                            )

    raise HTTPException(status_code=401, detail="Требуется вход в аккаунт с подключённым магазином.")


async def list_active_stores() -> list[StoreContext]:
    """
    Резолвит список сторов для фоновых воркеров (Phase 7) — system-internal вызов,
    без HTTP-запроса/JWT (в отличие от get_store_context, которому нужен Request).

    'Активный' = status != 'PAUSED' и есть расшифровываемый API-токен. Сторы со
    статусом COOKIE_EXPIRED всё равно включаются — это только про MC-куки
    (приватный API), REST-токен для чтения каталога/заказов у них обычно рабочий.

    Возвращает [] если в БД ещё нет ни одного Store (чистый dev-стенд без миграции,
    или прод до первого /connect-full-store) — вызывающий код сам решает, падать
    ли в этом случае на legacy глобальную сессию (states/session.json).
    """
    from backend.db.database import AsyncSessionLocal

    contexts: list[StoreContext] = []
    async with AsyncSessionLocal() as session:
        stmt = select(Store).where(Store.status != "PAUSED")
        res = await session.execute(stmt)
        stores = res.scalars().all()
        for store in stores:
            if not store.encrypted_api_key:
                continue
            token = decrypt_data(store.encrypted_api_key)
            if not token:
                continue
            contexts.append(StoreContext(token=token, merchant_id=store.merchant_id, store_id=store.id))
    return contexts


async def get_store_by_id(store_id: str) -> Optional[StoreContext]:
    """
    Резолвит StoreContext для конкретного store_id без HTTP-запроса/JWT —
    для system-internal вызовов, которые уже знают store_id (например,
    публичный 1С/ERP API, резолвящий его из API-ключа — см.
    routers/public_api/auth.py::get_api_key_user, Слайс 3).
    """
    from backend.db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        stmt = select(Store).where(Store.id == store_id)
        res = await session.execute(stmt)
        store = res.scalar_one_or_none()
        if not store or not store.encrypted_api_key:
            return None
        token = decrypt_data(store.encrypted_api_key)
        if not token:
            return None
        return StoreContext(token=token, merchant_id=store.merchant_id, store_id=store.id)


async def resolve_ws_store_id(websocket: WebSocket, db: AsyncSession) -> Optional[str]:
    """
    Резолвит store_id для WS-подключения (Phase 6). Браузерный WebSocket API не
    может слать кастомные заголовки (Authorization, X-Store-Id), поэтому и JWT,
    и выбранный магазин передаются через query-параметры (`?token=`, `?store_id=`,
    последний — Слайс 6) при подключении, а не в заголовках — отсюда отдельная
    функция, а не переиспользование get_store_context (которой нужен HTTP
    Request с Authorization header).

    При отсутствии/невалидности токена или нерезолвящемся сторе — возвращает
    None (legacy/общий телеметрийный канал, то же поведение, что было до
    Phase 6 для всех клиентов).
    """
    token_str = websocket.query_params.get("token")
    if not token_str:
        return None

    payload = decode_token(token_str)
    if not payload or payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    stmt = select(User).where(User.id == user_id)
    res = await db.execute(stmt)
    user = res.scalar_one_or_none()
    if not user or not user.is_active:
        return None

    # Слайс 6: ?store_id= — явный выбор магазина из UI (StoreHub/storeContext.ts).
    # Браузерный WebSocket не умеет заголовок X-Store-Id, отсюда query-параметр,
    # а не переиспользование _resolve_store_for_user's header-путь напрямую.
    requested_store_id = websocket.query_params.get("store_id")
    if requested_store_id:
        stmt = (
            select(Store)
            .join(UserStore, Store.id == UserStore.store_id)
            .where(Store.id == requested_store_id, UserStore.user_id == user.id)
        )
        res = await db.execute(stmt)
        requested_store = res.scalars().first()
        if requested_store:
            return requested_store.id
        # Запрошенный store_id не принадлежит этому пользователю — не
        # угадываем чужой магазин, падаем на обычный резолв ниже.

    store = await _resolve_store_for_user(websocket, db, user.id)
    return store.id if store else None
