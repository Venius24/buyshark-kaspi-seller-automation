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
from backend.security.store_roles import require_store_manager, store_section_guard
from backend.services.stock_sync_worker import execute_broadcast_sync, get_stock_lock
from backend.services import sku_recipes as recipes_lib

router = APIRouter(prefix="/api/stock-groups", tags=["stock-groups"], dependencies=[Depends(store_section_guard("stock-groups"))])

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

def _clean_recipes_or_400(group_id: str, raw, groups: dict, linked_skus: List[str],
                          strict: bool = True) -> dict:
    """Рецепты из запроса (pydantic или dict) -> проверенный словарь, иначе 400."""
    plain = {
        sku: (r.model_dump() if hasattr(r, "model_dump") else r)
        for sku, r in (raw or {}).items()
    }
    try:
        return recipes_lib.clean_recipes(group_id, plain, groups, linked_skus, strict=strict)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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
    ctx: StoreContext = Depends(require_store_manager),
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

    group_id = f"grp_{uuid.uuid4().hex[:8]}"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Все helper'ы состояния — синхронный psycopg2 поверх event loop'а.
    # _load_master_stock_groups на живом магазине (305 групп) стоит 606мс, и
    # вызванный напрямую из async-роутера он замораживает ВЕСЬ сервис на это
    # время. Отсюда же max_ms=14804 в мониторе лага лупа. Везде в этом файле
    # они уходят в asyncio.to_thread.
    expanded_skus = await asyncio.to_thread(_expand_linked_skus, req.linked_skus, ctx.store_id)
    new_group = {
        "group_id": group_id,
        "group_name": req.group_name.strip(),
        "shared_stock": int(req.shared_stock),
        "linked_skus": expanded_skus,
        "external_code": (req.external_code or "").strip() or None,
        "is_active": req.is_active if req.is_active is not None else True,
        "stop_qty": int(req.stop_qty) if req.stop_qty is not None else 5,
        "sku_recipes": {},
        "dumping_config": req.dumping_config.dict() if req.dumping_config else None,
        "dumping_excluded_skus": [s for s in (req.dumping_excluded_skus or []) if s in expanded_skus],
        "status": "updating",
        "created_at": now_str,
        "updated_at": now_str,
        "last_synced_at": None,
        "last_sync_log": "Группа создана, запуск веерной синхронизации..."
    }

    async with get_stock_lock(ctx.store_id):
        if req.sku_recipes:
            groups = await asyncio.to_thread(_load_master_stock_groups, store_id=ctx.store_id)
            new_group["sku_recipes"] = _clean_recipes_or_400(
                group_id, req.sku_recipes, groups, expanded_skus
            )
        # Пишем только новую группу: _save_master_stock_groups делает
        # session.get()+upsert на каждый ключ переданного словаря, и все
        # группы магазина ре-трогались бы ради создания одной.
        await asyncio.to_thread(
            _save_master_stock_groups, {group_id: new_group}, store_id=ctx.store_id
        )

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
    ctx: StoreContext = Depends(require_store_manager),
):
    """Обновить конфигурацию Мастер-Группы."""
    token = ctx.token

    if req.stop_qty is not None and req.stop_qty < 0:
        raise HTTPException(status_code=400, detail="Стоп кол-во не может быть отрицательным.")

    async with get_stock_lock(ctx.store_id):
        groups = await asyncio.to_thread(_load_master_stock_groups, store_id=ctx.store_id)
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
            group["linked_skus"] = await asyncio.to_thread(
                _expand_linked_skus, req.linked_skus, ctx.store_id
            )
        if "external_code" in provided:
            group["external_code"] = (req.external_code or "").strip() or None
        if req.is_active is not None:
            group["is_active"] = req.is_active
        if req.stop_qty is not None:
            group["stop_qty"] = int(req.stop_qty)
        # Рецепты чистятся и когда их не прислали: артикул, отвязанный от
        # группы, не должен оставлять за собой набор.
        if "sku_recipes" in provided:
            group["sku_recipes"] = _clean_recipes_or_400(
                group_id, req.sku_recipes, groups, group["linked_skus"]
            )
        else:
            group["sku_recipes"] = _clean_recipes_or_400(
                group_id, group.get("sku_recipes"), groups, group["linked_skus"], strict=False
            )
        if "dumping_config" in provided:
            new_cfg = req.dumping_config.model_dump() if req.dumping_config else None
            # Защиту от оттеснения форма группы может не прислать — тогда
            # сохраняем прежнюю, а не стираем её молча.
            if new_cfg is not None and "guard" not in req.dumping_config.model_fields_set:
                new_cfg["guard"] = (group.get("dumping_config") or {}).get("guard")
            group["dumping_config"] = new_cfg
        if req.dumping_excluded_skus is not None:
            group["dumping_excluded_skus"] = [s for s in req.dumping_excluded_skus if s in group["linked_skus"]]

        group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        group["status"] = "updating"
        # Только изменённая группа — см. комментарий в create_stock_group выше.
        await asyncio.to_thread(_save_master_stock_groups, {group_id: group}, store_id=ctx.store_id)

    background_tasks.add_task(execute_broadcast_sync, group_id, token, ctx.store_id)

    # Счётчика "на сборке" здесь больше нет. Он считался синхронным походом в
    # Kaspi (get_packaging_orders_for_skus тянет весь список заказов на сборке
    # по магазину, limit=1000) прямо в теле ответа — по замеру на проде это
    # 16.6с в среднем, то есть кнопка "Сохранить" столько и крутилась. То же
    # число модалка уже получила при открытии через GET /{group_id}/packaging
    # и дописывает его в тост сама.
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
    ctx: StoreContext = Depends(require_store_manager),
):
    """Быстрое изменение остатка у группы."""
    token = ctx.token
    if req.shared_stock < 0:
        raise HTTPException(status_code=400, detail="Остаток не может быть отрицательным.")

    async with get_stock_lock(ctx.store_id):
        groups = await asyncio.to_thread(_load_master_stock_groups, store_id=ctx.store_id)
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
        # Только изменённая группа — см. комментарий в create_stock_group выше.
        await asyncio.to_thread(_save_master_stock_groups, {group_id: group}, store_id=ctx.store_id)

    background_tasks.add_task(execute_broadcast_sync, group_id, token, ctx.store_id)

    return {
        "success": True,
        "message": f"Остаток группы '{group['group_name']}' изменен на {req.shared_stock} шт.",
        "group": group
    }

@router.delete("/{group_id}")
async def delete_stock_group(group_id: str, ctx: StoreContext = Depends(require_store_manager)):
    """Удалить Мастер-Группу."""
    async with get_stock_lock(ctx.store_id):
        groups = await asyncio.to_thread(_load_master_stock_groups, store_id=ctx.store_id)
        if group_id not in groups:
            raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

        deleted_group = groups.pop(group_id)
        # Наборы других групп, куда входила удалённая, иначе ссылались бы в пустоту.
        touched = recipes_lib.drop_group_refs(groups, group_id)
        # Именно _delete_master_stock_group: _save_master_stock_groups делает
        # только upsert и строку из БД не убирает — раньше группа "удалялась"
        # с зелёным тостом и возвращалась при следующем обновлении списка.
        if not await asyncio.to_thread(_delete_master_stock_group, group_id, ctx.store_id):
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось удалить Мастер-Группу '{deleted_group.get('group_name')}'.",
            )
        if touched:
            await asyncio.to_thread(_save_master_stock_groups, touched, store_id=ctx.store_id)

    from backend.services.stock_sync_worker import broadcast_ws_event
    await broadcast_ws_event('stock_group_updated', {'group_id': group_id}, store_id=ctx.store_id)

    return {
        "success": True,
        "message": f"Мастер-Группа '{deleted_group.get('group_name')}' успешно удалена."
    }

@router.post("/{group_id}/sync")
async def manual_sync_stock_group(
    group_id: str,
    background_tasks: BackgroundTasks,
    ctx: StoreContext = Depends(require_store_manager),
):
    """Принудительный запуск синхронизации группы."""
    token = ctx.token
    groups = await asyncio.to_thread(_load_master_stock_groups, store_id=ctx.store_id)
    if group_id not in groups:
        raise HTTPException(status_code=404, detail=f"Мастер-Группа {group_id} не найдена.")

    background_tasks.add_task(execute_broadcast_sync, group_id, token, ctx.store_id)
    return {"success": True, "message": "Запущена принудительная синхронизация остатков на Kaspi."}

@router.post("/check-packaging-deduction")
async def trigger_packaging_deduction(ctx: StoreContext = Depends(require_store_manager)):
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
