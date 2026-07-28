import uuid
import datetime
import asyncio
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

logger = logging.getLogger(__name__)

from backend.models import (
    MasterStockGroupCreateRequest,
    MasterStockGroupUpdateRequest,
    UpdateStockQtyRequest
)
from backend.state_helpers import (
    _load_master_stock_groups,
    _save_master_stock_groups
)
from backend.security.store_context import StoreContext, get_store_context
from backend.services.stock_sync_worker import execute_broadcast_sync, get_stock_lock

router = APIRouter(prefix="/api/stock-groups", tags=["stock-groups"])

import os
import json

def _expand_linked_skus(skus: List[str]) -> List[str]:
    """
    Расширяет список привязанных SKU, автоматически добавляя и merchantSku, и kaspiSku для каждого товара.
    """
    expanded = set()
    products = []
    products_file = "states/products.json"
    if os.path.exists(products_file):
        try:
            with open(products_file, "r", encoding="utf-8") as f:
                products = json.load(f)
        except Exception:
            logger.exception("Failed to load %s", products_file)
            products = []

    for raw_sku in skus:
        s = str(raw_sku).strip()
        if not s:
            continue
        expanded.add(s)
        
        # Находим товар в кэше по любому совпадению
        matched_p = next(
            (p for p in products if p.get("sku") == s or p.get("merchantSku") == s or p.get("kaspiSku") == s),
            None
        )
        if matched_p:
            if matched_p.get("sku"):
                expanded.add(str(matched_p["sku"]).strip())
            if matched_p.get("merchantSku"):
                expanded.add(str(matched_p["merchantSku"]).strip())
            if matched_p.get("kaspiSku"):
                expanded.add(str(matched_p["kaspiSku"]).strip())

    return list(expanded)

import time
from backend.kaspi_wrapper import KaspiAPI

_packaging_cache = {
    "data": None,
    "expires_at": 0.0
}

def _get_cached_packaging_data(token: str) -> dict:
    now = time.time()
    if _packaging_cache["data"] is not None and now < _packaging_cache["expires_at"]:
        return _packaging_cache["data"]

    try:
        data = KaspiAPI.get_all_packaging_tabs(token)
        _packaging_cache["data"] = data
        _packaging_cache["expires_at"] = now + 45.0
        return data
    except Exception:
        return _packaging_cache["data"] or {}

@router.get("")
def get_stock_groups(ctx: StoreContext = Depends(get_store_context)):
    """Получить список всех Мастер-Групп остатка с информацией об активных заказах в 'Упаковке'."""
    token = ctx.token
    groups_dict = _load_master_stock_groups(store_id=ctx.store_id)
    groups_list = list(groups_dict.values())

    # Выгружаем активную упаковку из кэша (TTL 45c) для предотвращения зависания эндпоинта
    packaging_data = _get_cached_packaging_data(token)
    cargo_assembly = packaging_data.get("cargo_assembly", [])

    for g in groups_list:
        skus = set([str(s).strip().lower() for s in g.get("linked_skus", [])])
        matched_orders = []
        total_items_qty = 0

        for order in cargo_assembly:
            items = order.get("items", [])
            matched = False
            for it in items:
                m_sku = str(it.get("merchant_sku", "")).strip().lower()
                title = str(it.get("title", "")).strip().lower()

                is_match = (
                    m_sku in skus
                    or any(s in m_sku or m_sku.startswith(s) or s in title for s in skus if len(s) >= 4)
                )

                if is_match:
                    matched = True
                    total_items_qty += it.get("quantity", 1)

            if matched:
                matched_orders.append(order)

        g["active_packaging_orders_count"] = len(matched_orders)
        g["active_packaging_items_qty"] = total_items_qty
        g["active_packaging_orders"] = matched_orders

    # Сортируем по дате обновления/создания
    groups_list.sort(key=lambda g: g.get("updated_at", ""), reverse=True)
    return {"success": True, "groups": groups_list, "count": len(groups_list)}

@router.get("/{group_id}/packaging")
def get_stock_group_packaging(group_id: str, ctx: StoreContext = Depends(get_store_context)):
    """Получить детальный список заказов в статусе 'Упаковка / На сборке' для конкретной Мастер-Группы."""
    token = ctx.token
    groups_dict = _load_master_stock_groups(store_id=ctx.store_id)
    if group_id not in groups_dict:
        raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

    group = groups_dict[group_id]
    linked_skus = group.get("linked_skus", [])
    
    res = KaspiAPI.get_packaging_orders_for_skus(token, linked_skus)
    return {
        "success": True,
        "group_id": group_id,
        "group_name": group.get("group_name"),
        "count": res["count"],
        "total_quantity": res["total_quantity"],
        "orders": res["orders"]
    }

@router.post("")
async def create_stock_group(
    req: MasterStockGroupCreateRequest,
    background_tasks: BackgroundTasks,
    ctx: StoreContext = Depends(get_store_context),
):
    """Создать новую Мастер-Группу остатка."""
    token = ctx.token

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
        "linked_skus": _expand_linked_skus(req.linked_skus),
        "external_code": (req.external_code or "").strip() or None,
        "is_active": req.is_active if req.is_active is not None else True,
        "dumping_config": req.dumping_config.dict() if req.dumping_config else None,
        "status": "updating",
        "created_at": now_str,
        "updated_at": now_str,
        "last_synced_at": None,
        "last_sync_log": "Группа создана, запуск веерной синхронизации..."
    }

    async with get_stock_lock(ctx.store_id):
        groups = _load_master_stock_groups(store_id=ctx.store_id)
        groups[group_id] = new_group
        _save_master_stock_groups(groups, store_id=ctx.store_id)

    # Запускаем фоновую асинхронную веерную синхронизацию
    background_tasks.add_task(execute_broadcast_sync, group_id, token, ctx.store_id)

    return {
        "success": True,
        "message": f"Мастер-Группа '{new_group['group_name']}' успешно создана.",
        "group": new_group
    }

@router.put("/{group_id}")
async def update_stock_group(
    group_id: str,
    req: MasterStockGroupUpdateRequest,
    background_tasks: BackgroundTasks,
    ctx: StoreContext = Depends(get_store_context),
):
    """Обновить конфигурацию Мастер-Группы."""
    token = ctx.token

    async with get_stock_lock(ctx.store_id):
        groups = _load_master_stock_groups(store_id=ctx.store_id)
        if group_id not in groups:
            raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

        group = groups[group_id]
        if req.group_name is not None and req.group_name.strip():
            group["group_name"] = req.group_name.strip()
        if req.shared_stock is not None and req.shared_stock >= 0:
            group["shared_stock"] = int(req.shared_stock)
        if req.linked_skus is not None and req.linked_skus:
            group["linked_skus"] = _expand_linked_skus(req.linked_skus)
        if req.external_code is not None:
            group["external_code"] = req.external_code.strip() or None
        if req.is_active is not None:
            group["is_active"] = req.is_active
        if req.dumping_config is not None:
            group["dumping_config"] = req.dumping_config.dict() if req.dumping_config else None

        group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        group["status"] = "updating"
        _save_master_stock_groups(groups, store_id=ctx.store_id)

    background_tasks.add_task(execute_broadcast_sync, group_id, token, ctx.store_id)

    return {
        "success": True,
        "message": f"Мастер-Группа '{group['group_name']}' обновлена.",
        "group": group
    }

@router.patch("/{group_id}/stock")
async def update_stock_qty(
    group_id: str,
    req: UpdateStockQtyRequest,
    background_tasks: BackgroundTasks,
    ctx: StoreContext = Depends(get_store_context),
):
    """Быстрое изменение остатка у группы."""
    token = ctx.token
    if req.shared_stock < 0:
        raise HTTPException(status_code=400, detail="Остаток не может быть отрицательным.")

    async with get_stock_lock(ctx.store_id):
        groups = _load_master_stock_groups(store_id=ctx.store_id)
        if group_id not in groups:
            raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

        group = groups[group_id]
        group["shared_stock"] = int(req.shared_stock)
        group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        group["status"] = "updating"
        group["last_sync_log"] = f"Ручной ввод остатка: {req.shared_stock} шт."
        _save_master_stock_groups(groups, store_id=ctx.store_id)

    background_tasks.add_task(execute_broadcast_sync, group_id, token, ctx.store_id)

    return {
        "success": True,
        "message": f"Остаток группы '{group['group_name']}' изменен на {req.shared_stock} шт.",
        "group": group
    }

@router.delete("/{group_id}")
async def delete_stock_group(group_id: str, ctx: StoreContext = Depends(get_store_context)):
    """Удалить Мастер-Группу."""
    async with get_stock_lock(ctx.store_id):
        groups = _load_master_stock_groups(store_id=ctx.store_id)
        if group_id not in groups:
            raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

        deleted_group = groups.pop(group_id)
        _save_master_stock_groups(groups, store_id=ctx.store_id)

    return {
        "success": True,
        "message": f"Мастер-Группа '{deleted_group.get('group_name')}' успешно удалена."
    }

@router.post("/{group_id}/sync")
async def manual_sync_stock_group(
    group_id: str,
    background_tasks: BackgroundTasks,
    ctx: StoreContext = Depends(get_store_context),
):
    """Принудительный запуск синхронизации группы."""
    token = ctx.token
    groups = _load_master_stock_groups(store_id=ctx.store_id)
    if group_id not in groups:
        raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

    background_tasks.add_task(execute_broadcast_sync, group_id, token, ctx.store_id)
    return {"success": True, "message": "Запущена принудительная синхронизация остатков на Kaspi."}

@router.post("/check-packaging-deduction")
async def trigger_packaging_deduction(ctx: StoreContext = Depends(get_store_context)):
    """Принудительный запуск проверки и авто-списания остатков по новым заказам в Упаковке."""
    token = ctx.token
    from backend.services.stock_sync_worker import process_packaging_auto_deduction
    try:
        res = await process_packaging_auto_deduction(token, ctx.store_id)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

