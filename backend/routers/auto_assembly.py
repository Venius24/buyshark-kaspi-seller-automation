import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.models import AutoAssemblyRuleRequest, MarkSpoolPrintedRequest
from backend.security.store_context import StoreContext, get_store_context
from backend.state_helpers import _get_sync_db_session
from backend.services.auto_assembly_worker import (
    run_auto_assembly_cycle,
    get_unprinted_spool_rows,
    build_spool_zip_bytes,
    mark_spool_printed,
)
from backend.services.print_batcher_service import process_zip_or_pdfs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auto-assembly", tags=["auto-assembly"])

# Те же 6 категорий, что возвращает KaspiAPI._parse_delivery_type — единый
# список для формирования полной сетки правил на фронте, даже если у стора
# ещё нет ни одной строки в auto_assembly_rules.
DELIVERY_TYPES = ["Express", "Postomat", "Самовывоз", "Today", "Tomorrow", "Kaspi Доставка"]


def _default_rule_dict(delivery_type: str) -> dict:
    return {
        "delivery_type": delivery_type,
        "enabled": False,
        "dry_run": True,
        "stage_accept": False,
        "stage_assemble": False,
        "stage_waybill": False,
        "schedule": None,
        "max_order_price": None,
        "sku_blacklist": [],
        "respect_stock_groups": True,
        "max_per_cycle": None,
    }


def _row_to_dict(row) -> dict:
    return {
        "delivery_type": row.delivery_type,
        "enabled": row.enabled,
        "dry_run": row.dry_run,
        "stage_accept": row.stage_accept,
        "stage_assemble": row.stage_assemble,
        "stage_waybill": row.stage_waybill,
        "schedule": json.loads(row.schedule) if row.schedule else None,
        "max_order_price": row.max_order_price,
        "sku_blacklist": json.loads(row.sku_blacklist) if row.sku_blacklist else [],
        "respect_stock_groups": row.respect_stock_groups,
        "max_per_cycle": row.max_per_cycle,
    }


@router.get("/rules")
def get_rules(ctx: StoreContext = Depends(get_store_context)):
    """Полная сетка правил по всем видам доставки — незаполненные виды
    возвращаются с дефолтами (enabled=False), чтобы фронт всегда рисовал
    все карточки."""
    session = _get_sync_db_session()
    by_type = {}
    if session:
        try:
            from sqlalchemy import select
            from backend.db.models import AutoAssemblyRuleDB
            rows = session.scalars(
                select(AutoAssemblyRuleDB).where(AutoAssemblyRuleDB.store_id == ctx.store_id)
            ).all()
            by_type = {r.delivery_type: _row_to_dict(r) for r in rows}
        finally:
            session.close()

    rules = [by_type.get(dt, _default_rule_dict(dt)) for dt in DELIVERY_TYPES]
    return {"success": True, "rules": rules}


@router.put("/rules")
def put_rule(req: AutoAssemblyRuleRequest, ctx: StoreContext = Depends(get_store_context)):
    """Создать/обновить правило одного вида доставки (upsert по
    store_id+delivery_type)."""
    if req.delivery_type not in DELIVERY_TYPES:
        raise HTTPException(status_code=400, detail=f"Неизвестный вид доставки: {req.delivery_type}")

    session = _get_sync_db_session()
    if not session:
        raise HTTPException(status_code=500, detail="БД недоступна")
    try:
        from backend.db.models import AutoAssemblyRuleDB
        row = session.get(AutoAssemblyRuleDB, (ctx.store_id, req.delivery_type))
        if row is None:
            row = AutoAssemblyRuleDB(store_id=ctx.store_id, delivery_type=req.delivery_type)
            session.add(row)

        row.enabled = req.enabled
        row.dry_run = req.dry_run
        row.stage_accept = req.stage_accept
        row.stage_assemble = req.stage_assemble
        row.stage_waybill = req.stage_waybill
        row.schedule = json.dumps(req.schedule, ensure_ascii=False) if req.schedule else None
        row.max_order_price = req.max_order_price
        row.sku_blacklist = json.dumps(req.sku_blacklist or [], ensure_ascii=False)
        row.respect_stock_groups = req.respect_stock_groups
        row.max_per_cycle = req.max_per_cycle
        row.updated_at = datetime.now()
        session.commit()
        return {"success": True, "rule": _row_to_dict(row)}
    finally:
        session.close()


@router.get("/log")
def get_log(limit: int = Query(100, le=500), ctx: StoreContext = Depends(get_store_context)):
    session = _get_sync_db_session()
    if not session:
        return {"success": True, "entries": []}
    try:
        from sqlalchemy import select
        from backend.db.models import AutoAssemblyLogDB
        rows = session.scalars(
            select(AutoAssemblyLogDB)
            .where(AutoAssemblyLogDB.store_id == ctx.store_id)
            .order_by(AutoAssemblyLogDB.id.desc())
            .limit(limit)
        ).all()
        entries = [
            {
                "order_code": r.order_code,
                "stage": r.stage,
                "decision": r.decision,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
        return {"success": True, "entries": entries}
    finally:
        session.close()


@router.post("/run-once")
async def run_once(ctx: StoreContext = Depends(get_store_context)):
    """Ручной прогон одного цикла автосборки вне 30с фонового тика —
    удобно для проверки правила сразу после сохранения."""
    try:
        await run_auto_assembly_cycle(ctx.token, ctx.store_id)
        return {"success": True, "message": "Цикл автосборки выполнен."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/spool")
def get_spool(ctx: StoreContext = Depends(get_store_context)):
    rows = get_unprinted_spool_rows(ctx.store_id)
    return {
        "success": True,
        "count": len(rows),
        "items": [
            {"order_code": r["order_code"], "date": r["date"]}
            for r in rows
        ],
    }


@router.post("/spool/download")
def download_spool(
    output_layout: str = Query("auto"),
    ctx: StoreContext = Depends(get_store_context),
):
    """Склеивает все ещё не напечатанные накладные стора в пачку (тем же
    PDF-пайплайном, что и ручная загрузка в PrintBatcher) — склейка
    происходит здесь, по клику, а не при каждом скачивании накладной."""
    rows = get_unprinted_spool_rows(ctx.store_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Нет накопленных накладных для печати")

    zip_bytes = build_spool_zip_bytes(ctx.store_id)
    try:
        manifest = process_zip_or_pdfs(
            file_bytes=zip_bytes,
            original_filename="auto_assembly_spool.zip",
            sort_preset="order_id",
            auto_rotate=True,
            express_mode="separate",
            deduplicate_history=True,
            force_reprint=False,
            output_layout=output_layout,
            store_id=ctx.store_id,
        )
        return JSONResponse(content=manifest)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка склейки пачки накладных: {str(e)}")


@router.post("/spool/mark-printed")
def mark_printed(req: MarkSpoolPrintedRequest, ctx: StoreContext = Depends(get_store_context)):
    count = mark_spool_printed(ctx.store_id, order_codes=req.order_codes)
    return {"success": True, "marked_count": count}
