import uuid
import datetime
import asyncio
from typing import Optional, List
from fastapi import APIRouter, HTTPException, BackgroundTasks

from backend.models import (
    MasterStockGroupCreateRequest,
    MasterStockGroupUpdateRequest,
    UpdateStockQtyRequest
)
from backend.state_helpers import (
    _require_token,
    _load_master_stock_groups,
    _save_master_stock_groups
)
from backend.services.stock_sync_worker import execute_broadcast_sync, stock_lock

router = APIRouter(prefix="/api/stock-groups", tags=["stock-groups"])

@router.get("")
def get_stock_groups():
    """Получить список всех Мастер-Групп остатка."""
    _require_token()
    groups_dict = _load_master_stock_groups()
    groups_list = list(groups_dict.values())
    
    # Сортируем по дате обновления/создания
    groups_list.sort(key=lambda g: g.get("updated_at", ""), reverse=True)
    return {"success": True, "groups": groups_list, "count": len(groups_list)}

@router.post("")
async def create_stock_group(req: MasterStockGroupCreateRequest, background_tasks: BackgroundTasks):
    """Создать новую Мастер-Группу остатка."""
    token = _require_token()
    
    if not req.group_name.strip():
        raise HTTPException(status_code=400, detail="Название группы не может быть пустым.")
    if req.shared_stock < 0:
        raise HTTPException(status_code=400, detail="Остаток не может быть отрицательным.")
    if not req.linked_skus:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один товар (SKU) для связывания.")

    group_id = f"grp_{uuid.uuid4().hex[:8]}"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    new_group = {
        "group_id": group_id,
        "group_name": req.group_name.strip(),
        "shared_stock": int(req.shared_stock),
        "linked_skus": [sku.strip() for sku in req.linked_skus if sku.strip()],
        "external_code": (req.external_code or "").strip() or None,
        "is_active": req.is_active if req.is_active is not None else True,
        "dumping_config": req.dumping_config.dict() if req.dumping_config else None,
        "status": "updating",
        "created_at": now_str,
        "updated_at": now_str,
        "last_synced_at": None,
        "last_sync_log": "Группа создана, запуск веерной синхронизации..."
    }

    async with stock_lock:
        groups = _load_master_stock_groups()
        groups[group_id] = new_group
        _save_master_stock_groups(groups)

    # Запускаем фоновую асинхронную веерную синхронизацию
    background_tasks.add_task(execute_broadcast_sync, group_id, token)

    return {
        "success": True,
        "message": f"Мастер-Группа '{new_group['group_name']}' успешно создана.",
        "group": new_group
    }

@router.put("/{group_id}")
async def update_stock_group(group_id: str, req: MasterStockGroupUpdateRequest, background_tasks: BackgroundTasks):
    """Обновить конфигурацию Мастер-Группы."""
    token = _require_token()
    
    async with stock_lock:
        groups = _load_master_stock_groups()
        if group_id not in groups:
            raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

        group = groups[group_id]
        if req.group_name is not None and req.group_name.strip():
            group["group_name"] = req.group_name.strip()
        if req.shared_stock is not None and req.shared_stock >= 0:
            group["shared_stock"] = int(req.shared_stock)
        if req.linked_skus is not None and req.linked_skus:
            group["linked_skus"] = [sku.strip() for sku in req.linked_skus if sku.strip()]
        if req.external_code is not None:
            group["external_code"] = req.external_code.strip() or None
        if req.is_active is not None:
            group["is_active"] = req.is_active
        if req.dumping_config is not None:
            group["dumping_config"] = req.dumping_config.dict() if req.dumping_config else None

        group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        group["status"] = "updating"
        _save_master_stock_groups(groups)

    background_tasks.add_task(execute_broadcast_sync, group_id, token)

    return {
        "success": True,
        "message": f"Мастер-Группа '{group['group_name']}' обновлена.",
        "group": group
    }

@router.patch("/{group_id}/stock")
async def update_stock_qty(group_id: str, req: UpdateStockQtyRequest, background_tasks: BackgroundTasks):
    """Быстрое изменение остатка у группы."""
    token = _require_token()
    if req.shared_stock < 0:
        raise HTTPException(status_code=400, detail="Остаток не может быть отрицательным.")

    async with stock_lock:
        groups = _load_master_stock_groups()
        if group_id not in groups:
            raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

        group = groups[group_id]
        group["shared_stock"] = int(req.shared_stock)
        group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        group["status"] = "updating"
        group["last_sync_log"] = f"Ручной ввод остатка: {req.shared_stock} шт."
        _save_master_stock_groups(groups)

    background_tasks.add_task(execute_broadcast_sync, group_id, token)

    return {
        "success": True,
        "message": f"Остаток группы '{group['group_name']}' изменен на {req.shared_stock} шт.",
        "group": group
    }

@router.delete("/{group_id}")
async def delete_stock_group(group_id: str):
    """Удалить Мастер-Группу."""
    _require_token()
    async with stock_lock:
        groups = _load_master_stock_groups()
        if group_id not in groups:
            raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

        deleted_group = groups.pop(group_id)
        _save_master_stock_groups(groups)

    return {
        "success": True,
        "message": f"Мастер-Группа '{deleted_group.get('group_name')}' успешно удалена."
    }

@router.post("/{group_id}/sync")
async def manual_sync_stock_group(group_id: str, background_tasks: BackgroundTasks):
    """Принудительный запуск синхронизации группы."""
    token = _require_token()
    groups = _load_master_stock_groups()
    if group_id not in groups:
        raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

    background_tasks.add_task(execute_broadcast_sync, group_id, token)
    return {"success": True, "message": "Запущена принудительная синхронизация остатков на Kaspi."}
