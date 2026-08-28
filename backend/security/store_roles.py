"""Проверка роли внутри магазина (UserStore.role) — ортогонально
platform_roles.py (User.is_superadmin, платформенный доступ). require_store_role
навешивается на мутирующие эндпоинты store-scoped роутеров дополнительно к
get_store_context/get_store_context_billed, не заменяя их логику резолва
магазина — только читает role, который те уже кладут в StoreContext."""
from fastapi import Depends, HTTPException, status

from backend.security.store_context import StoreContext, get_store_context, get_store_context_billed

ROLE_RANK = {"VIEWER": 0, "MANAGER": 1, "OWNER": 2}


def require_store_role(min_role: str, billed: bool = False):
    base_dep = get_store_context_billed if billed else get_store_context

    async def dep(ctx: StoreContext = Depends(base_dep)) -> StoreContext:
        if ROLE_RANK.get(ctx.role, 0) < ROLE_RANK[min_role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для этого действия")
        return ctx

    return dep


require_store_manager = require_store_role("MANAGER")
require_store_manager_billed = require_store_role("MANAGER", billed=True)
require_store_owner = require_store_role("OWNER")
