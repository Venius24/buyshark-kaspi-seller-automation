import logging
from dataclasses import dataclass
from typing import Optional
from fastapi import Request, Depends
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
