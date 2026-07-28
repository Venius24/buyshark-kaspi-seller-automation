import logging
from dataclasses import dataclass
from typing import Optional
from fastapi import Request, WebSocket, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import User, Store, UserStore
from backend.security.auth import decode_token
from backend.security.crypto import decrypt_data
from backend.state_helpers import _require_token
from backend.dumper.config import discover_merchant_id

logger = logging.getLogger(__name__)


@dataclass
class StoreContext:
    token: str
    merchant_id: str
    store_id: Optional[str]  # None only for legacy (pre-Phase-4e) callers


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
    Резолвит (token, merchant_id, store_id) для эндпоинтов, мигрирующих на
    per-store модель (Phase 4b-4d refactor plan).

    Переходный период (dual-auth, до Phase 4e — удаления legacy-пути):
    если запрос несёт валидный JWT Bearer, резолвящийся в активного User,
    и у пользователя есть доступный Store (через заголовок X-Store-Id или
    единственный/старейший магазин) — используем новую, per-store схему.
    Иначе — падаем обратно на legacy глобальную сессию (states/session.json),
    ТОЧНО то же поведение, что и текущий _require_token()+discover_merchant_id().
    Ничего не ломается для клиентов, ещё не отправляющих JWT.
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

    # Legacy fallback — unchanged behavior for pre-migration clients.
    legacy_token = _require_token()
    legacy_merchant_id = discover_merchant_id(legacy_token)
    return StoreContext(token=legacy_token, merchant_id=legacy_merchant_id, store_id=None)


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


async def resolve_ws_store_id(websocket: WebSocket, db: AsyncSession) -> Optional[str]:
    """
    Резолвит store_id для WS-подключения (Phase 6). Браузерный WebSocket API не
    может слать кастомные заголовки (Authorization), поэтому JWT передаётся
    через query-параметр `?token=` при подключении, а не в заголовке — отсюда
    отдельная функция, а не переиспользование get_store_context (которой нужен
    HTTP Request с Authorization header).

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

    store = await _resolve_store_for_user(websocket, db, user.id)
    return store.id if store else None
