import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from backend.models import PublicStockUpdateRequest
from backend.state_helpers import (
    _load_master_stock_groups,
    _save_master_stock_groups
)
from backend.routers.public_api.auth import get_api_key_user
from backend.security.store_context import get_store_by_id
from backend.services.stock_sync_worker import execute_broadcast_sync, get_stock_lock
from backend.services.webhook_dispatcher import dispatch_webhook_event

router = APIRouter(prefix="/api/v1/public", tags=["public-api"])

@router.get("/groups")
def get_public_groups(auth_user: dict = Depends(get_api_key_user)):
    """
    [Public API 1С/ERP] Получить список Мастер-Групп остатков ТОЛЬКО того
    магазина, которому принадлежит предъявленный API-ключ (Слайс 3 — раньше
    любой валидный ключ видел группы ВСЕХ магазинов).
    """
    groups_dict = _load_master_stock_groups(store_id=auth_user["store_id"])
    data = []

    for gid, g in groups_dict.items():
        data.append({
            "group_id": gid,
            "group_name": g.get("group_name"),
            "external_code": g.get("external_code"),
            "shared_stock": g.get("shared_stock", 0),
            "linked_skus": g.get("linked_skus", []),
            "linked_skus_count": len(g.get("linked_skus", [])),
            "is_active": g.get("is_active", True),
            "status": g.get("status", "synced"),
            "updated_at": g.get("updated_at")
        })

    return {"success": True, "count": len(data), "data": data}

@router.post("/stocks/update")
async def update_public_stock(
    req: PublicStockUpdateRequest,
    background_tasks: BackgroundTasks,
    auth_user: dict = Depends(get_api_key_user)
):
    """
    [Public API 1С/ERP] Обновить остаток Мастер-Группы по group_id или external_code,
    ТОЛЬКО в рамках магазина предъявленного API-ключа (Слайс 3).
    Поддерживает режим SET (установка точного остатка) и DELTA (списание/приход +/- N).
    """
    if not req.group_id and not req.external_code:
        raise HTTPException(
            status_code=400,
            detail="Необходимо указать хотя бы одно из полей: group_id или external_code."
        )

    store_id = auth_user["store_id"]
    store_ctx = await get_store_by_id(store_id)
    if not store_ctx:
        raise HTTPException(
            status_code=400,
            detail="Магазин этого API-ключа не найден или не авторизован в Kaspi MC — обратитесь в поддержку."
        )
    token = store_ctx.token

    async with get_stock_lock(store_id):
        groups = _load_master_stock_groups(store_id=store_id)

        matched_gid = None
        target_group = None

        if req.group_id and req.group_id in groups:
            matched_gid = req.group_id
            target_group = groups[matched_gid]
        elif req.external_code:
            for gid, g in groups.items():
                if g.get("external_code") == req.external_code.strip():
                    matched_gid = gid
                    target_group = g
                    break

        if not target_group:
            err_id = req.group_id or req.external_code
            raise HTTPException(
                status_code=404,
                detail=f"Мастер-Группа с идентификатором '{err_id}' не найдена."
            )

        mode = (req.mode or "SET").upper()
        current_stock = int(target_group.get("shared_stock", 0))

        if mode == "DELTA":
            new_stock = max(0, current_stock + req.new_stock)
        else:
            new_stock = max(0, req.new_stock)

        target_group["shared_stock"] = new_stock
        target_group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        target_group["status"] = "updating"
        target_group["last_sync_log"] = f"Обновлено через Public API ({mode}): {new_stock} шт."

        groups[matched_gid] = target_group
        _save_master_stock_groups(groups, store_id=store_id)

    # Запускаем веерное обновление на Kaspi
    background_tasks.add_task(execute_broadcast_sync, matched_gid, token, store_id)

    # Отправляем Webhook событие во внешнюю 1С (конфиг этого же магазина)
    background_tasks.add_task(
        dispatch_webhook_event,
        "stock.updated",
        {
            "group_id": matched_gid,
            "group_name": target_group.get("group_name"),
            "external_code": target_group.get("external_code"),
            "previous_stock": current_stock,
            "new_stock": new_stock,
            "mode": mode,
            "source": f"Public API ({auth_user.get('name')})"
        },
        store_id
    )

    linked_count = len(target_group.get("linked_skus", []))
    msg = f"Остаток обновлен на {new_stock} шт. Запущена синхронизация {linked_count} SKU на Kaspi."

    return {
        "success": True,
        "message": msg,
        "group_id": matched_gid,
        "external_code": target_group.get("external_code"),
        "updated_stock": new_stock,
        "mode": mode
    }
