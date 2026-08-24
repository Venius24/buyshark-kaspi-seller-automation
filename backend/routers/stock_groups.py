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
    _save_master_stock_groups,
    _delete_master_stock_group,
    _load_products,
)
from backend.security.store_context import StoreContext, get_store_context
from backend.services.stock_sync_worker import execute_broadcast_sync, get_stock_lock

router = APIRouter(prefix="/api/stock-groups", tags=["stock-groups"])

def _expand_linked_skus(skus: List[str], store_id: Optional[str]) -> List[str]:
    """
    Расширяет список привязанных SKU, автоматически добавляя и merchantSku, и kaspiSku для каждого товара.
    """
    expanded = set()
    products = _load_products(store_id)

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

from backend.kaspi_wrapper import KaspiAPI

@router.get("")
def get_stock_groups(ctx: StoreContext = Depends(get_store_context)):
    """Получить список всех Мастер-Групп остатка."""
    groups_dict = _load_master_stock_groups(store_id=ctx.store_id)
    groups_list = list(groups_dict.values())

    # Раньше здесь на каждый запрос гонялся матчинг «группа × заказ × позиция»
    # с подстрочными сравнениями, а в ответ клался ПОЛНЫЙ список заказов
    # упаковки отдельно для каждой группы (289 копий). Ни фронтенд (тип
    # MasterStockGroup не содержит active_packaging_*), ни бэкенд, ни тесты
    # эти поля не читали — это был главный источник p95=27.6с у ручки
    # (HANDOFF §3.52 «Осталось»). Детальный список заказов группы по кнопке
    # живёт в GET /{group_id}/packaging, он и остаётся единственным путём.
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
    if req.stop_qty is not None and req.stop_qty < 0:
        raise HTTPException(status_code=400, detail="Стоп кол-во не может быть отрицательным.")
    if req.deduct_per_unit is not None and req.deduct_per_unit < 1:
        raise HTTPException(status_code=400, detail="Списание за единицу не может быть меньше 1 шт.")

    group_id = f"grp_{uuid.uuid4().hex[:8]}"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    expanded_skus = _expand_linked_skus(req.linked_skus, ctx.store_id)
    new_group = {
        "group_id": group_id,
        "group_name": req.group_name.strip(),
        "shared_stock": int(req.shared_stock),
        "linked_skus": expanded_skus,
        "external_code": (req.external_code or "").strip() or None,
        "is_active": req.is_active if req.is_active is not None else True,
        "stop_qty": int(req.stop_qty) if req.stop_qty is not None else 5,
        "deduct_per_unit": int(req.deduct_per_unit) if req.deduct_per_unit else 1,
        "dumping_config": req.dumping_config.dict() if req.dumping_config else None,
        "dumping_excluded_skus": [s for s in (req.dumping_excluded_skus or []) if s in expanded_skus],
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

    if req.stop_qty is not None and req.stop_qty < 0:
        raise HTTPException(status_code=400, detail="Стоп кол-во не может быть отрицательным.")
    if req.deduct_per_unit is not None and req.deduct_per_unit < 1:
        raise HTTPException(status_code=400, detail="Списание за единицу не может быть меньше 1 шт.")

    async with get_stock_lock(ctx.store_id):
        groups = _load_master_stock_groups(store_id=ctx.store_id)
        if group_id not in groups:
            raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

        # "Поле не прислали" и "поле прислали пустым" — разные намерения, а по
        # значению None они неразличимы. Для полей, которые пользователь
        # должен уметь СБРОСИТЬ, смотрим на model_fields_set: раньше
        # выключение демпинга у группы (фронт шлёт dumping_config: null) молча
        # игнорировалось — форма закрывалась с "успешно обновлена", а группа
        # продолжала демпинговаться со старыми настройками.
        provided = req.model_fields_set

        group = groups[group_id]
        if req.group_name is not None and req.group_name.strip():
            group["group_name"] = req.group_name.strip()
        if req.shared_stock is not None and req.shared_stock >= 0:
            group["shared_stock"] = int(req.shared_stock)
            group["oversold"] = 0  # пересчёт по факту склада закрывает долг
        if req.linked_skus is not None and req.linked_skus:
            group["linked_skus"] = _expand_linked_skus(req.linked_skus, ctx.store_id)
        if "external_code" in provided:
            group["external_code"] = (req.external_code or "").strip() or None
        if req.is_active is not None:
            group["is_active"] = req.is_active
        if req.stop_qty is not None:
            group["stop_qty"] = int(req.stop_qty)
        if req.deduct_per_unit is not None:
            group["deduct_per_unit"] = int(req.deduct_per_unit)
        if "dumping_config" in provided:
            group["dumping_config"] = req.dumping_config.model_dump() if req.dumping_config else None
        if req.dumping_excluded_skus is not None:
            group["dumping_excluded_skus"] = [s for s in req.dumping_excluded_skus if s in group["linked_skus"]]

        group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        group["status"] = "updating"
        _save_master_stock_groups(groups, store_id=ctx.store_id)

    background_tasks.add_task(execute_broadcast_sync, group_id, token, ctx.store_id)

    # Счётчик "на сборке" — только здесь, в ответ на РУЧНОЕ редактирование
    # группы в UI. Специально не переносим это в execute_broadcast_sync: та же
    # функция запускается на КАЖДЫЙ заказ (trigger_order_info), а
    # get_packaging_orders_for_skus тянет из Kaspi весь список заказов на
    # сборке по магазину (limit=1000) — именно такой запрос на каждую продажу
    # уже давал p95=27.6с на ручке списка групп (см. комментарий в
    # get_stock_groups выше).
    packaging_note = ""
    try:
        packaging = KaspiAPI.get_packaging_orders_for_skus(token, group["linked_skus"])
        packaging_note = f" На сборке: {packaging['count']} зак. ({packaging['total_quantity']} шт.)."
    except Exception as e:
        logger.warning("Не удалось посчитать заказы на сборке для группы %s: %s", group_id, e)

    return {
        "success": True,
        "message": f"Мастер-Группа '{group['group_name']}' обновлена.{packaging_note}",
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
        # Ручной ввод — это пересчёт по факту склада, он закрывает долг
        # перепродажи: владелец уже видит реальное число штук.
        debt = int(group.get("oversold", 0))
        group["oversold"] = 0
        group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        group["status"] = "updating"
        group["last_sync_log"] = (
            f"Ручной ввод остатка: {req.shared_stock} шт."
            + (f" (долг {debt} шт. списан)" if debt else "")
        )
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
        # Именно _delete_master_stock_group: _save_master_stock_groups делает
        # только upsert и строку из БД не убирает — раньше группа "удалялась"
        # с зелёным тостом и возвращалась при следующем обновлении списка.
        if not _delete_master_stock_group(group_id, ctx.store_id):
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось удалить Мастер-Группу '{deleted_group.get('group_name')}'.",
            )

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
    from backend import feature_flags
    if not feature_flags.is_stock_groups_enabled(ctx.store_id):
        raise HTTPException(status_code=409, detail="Связка остатков отключена в Глобальном управлении.")
    token = ctx.token
    from backend.services.stock_sync_worker import process_packaging_auto_deduction
    try:
        res = await process_packaging_auto_deduction(token, ctx.store_id)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

