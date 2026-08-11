"""
Роутер фичи «Автодобавление товара» — единственное место, где встречаются
async backend.autolist.extractor (внешний OpenAI API, httpx) и sync
backend.autolist.category_mapper (mc.shop.kaspi.kz, requests). Sync-вызовы
оборачиваются в asyncio.to_thread, чтобы не блокировать event loop — тот же
паттерн, что print_batcher.py использует для тяжёлого PyMuPDF-пайплайна.

См. backend/autolist/validator.py (docstring create_draft) и HANDOFF.md 3.17
про то, почему эта склейка не живёт в validator.py напрямую.
"""

import asyncio
import functools
import logging
import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.autolist import category_mapper as cm
from backend.autolist import extractor
from backend.autolist import validator as v
from backend.models import AutolistDraftPatchRequest
from backend.security.store_context import StoreContext, get_store_context, get_store_context_billed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/autolist", tags=["autolist"])


@router.post("/drafts")
async def create_draft(
    photo: UploadFile = File(...),
    title: str = Form(...),
    ctx: StoreContext = Depends(get_store_context),
):
    """Загрузка фото+название → полный AI-пайплайн → черновик в 'needs_review'.
    Ничего не отправляется в Kaspi на этом шаге."""
    if not photo.filename:
        raise HTTPException(status_code=400, detail="Файл не передан")
    ext = os.path.splitext(photo.filename)[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(status_code=400, detail="Поддерживаются только JPG/PNG/WEBP")

    content = await photo.read()
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        merchant_id = await asyncio.to_thread(cm.resolve_merchant_id, ctx.token)

        guess = await extractor.guess_category(tmp_path, title)
        category_query = guess.get("category_query") or title

        categories = await asyncio.to_thread(cm.search_categories, category_query, merchant_id, ctx.store_id)
        existing_candidates = await asyncio.to_thread(cm.search_existing_products, category_query, ctx.store_id)

        if not categories:
            # Честно признаём, что не смогли найти категорию, а не гадаем —
            # черновик всё равно создаётся, но без characteristics-схемы,
            # human должен будет подобрать категорию вручную (UI TODO).
            ai_raw = {"guess_category": guess}
            draft_payload = {
                "category_code": None,
                "category_name": None,
                "brand_name": guess.get("brand_guess"),
                "sku": None,
                "price": None,
                "stock": None,
                "features": {},
                "feature_meta": {},
                "classifications": [],
                "description": "",
                "existing_candidates": existing_candidates,
                "category_search_query": category_query,
            }
            draft = await asyncio.to_thread(v.save_draft, ctx.store_id, tmp_path, title, ai_raw, draft_payload)
            draft["warning"] = f"Kaspi не нашёл категорию по запросу {category_query!r} — выберите категорию вручную"
            return draft

        category = categories[0]
        schema = await asyncio.to_thread(cm.fetch_category_schema, category["code"], merchant_id, ctx.store_id)
        fill = await extractor.fill_attributes(tmp_path, title, schema, brand_hint=guess.get("brand_guess") or "")
        sku = await asyncio.to_thread(cm.generate_sku, merchant_id, ctx.store_id)

        ai_raw = {"guess_category": guess, "fill_attributes": fill}
        draft_payload = {
            "category_code": category["code"],
            "category_name": category["name"],
            "brand_name": guess.get("brand_guess"),
            "sku": sku,
            "price": None,
            "stock": None,
            "features": fill["features"],
            "feature_meta": fill["feature_meta"],
            "classifications": schema.get("classifications", []),
            "description": fill.get("description", ""),
            "existing_candidates": existing_candidates,
            "category_search_query": category_query,
        }
        return await asyncio.to_thread(v.save_draft, ctx.store_id, tmp_path, title, ai_raw, draft_payload)
    except Exception as e:
        logger.error(f"create_draft провалился: {e}")
        raise HTTPException(status_code=502, detail=f"Не удалось обработать товар: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.get("/drafts")
async def list_drafts(status: str = None, ctx: StoreContext = Depends(get_store_context)):
    return await asyncio.to_thread(v.list_drafts, ctx.store_id, status)


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: str, ctx: StoreContext = Depends(get_store_context)):
    try:
        return await asyncio.to_thread(v.get_draft, draft_id, ctx.store_id)
    except v.DraftNotFoundError:
        raise HTTPException(status_code=404, detail="Черновик не найден")


@router.get("/drafts/{draft_id}/photo")
async def get_draft_photo(draft_id: str, ctx: StoreContext = Depends(get_store_context)):
    try:
        draft = await asyncio.to_thread(v.get_draft, draft_id, ctx.store_id)
    except v.DraftNotFoundError:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    if not os.path.exists(draft["photo_path"]):
        raise HTTPException(status_code=404, detail="Файл фото не найден")
    return FileResponse(draft["photo_path"])


@router.patch("/drafts/{draft_id}")
async def update_draft(draft_id: str, body: AutolistDraftPatchRequest, ctx: StoreContext = Depends(get_store_context)):
    try:
        return await asyncio.to_thread(v.update_draft, draft_id, ctx.store_id, body.patch)
    except v.DraftNotFoundError:
        raise HTTPException(status_code=404, detail="Черновик не найден")


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(draft_id: str, ctx: StoreContext = Depends(get_store_context)):
    try:
        return await asyncio.to_thread(v.approve_draft, draft_id, ctx.store_id)
    except v.DraftNotFoundError:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    except v.DraftNotApprovedError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/drafts/{draft_id}/submit")
async def submit_draft(draft_id: str, ctx: StoreContext = Depends(get_store_context_billed)):
    """Реальная отправка в Kaspi — требует, чтобы черновик уже был в статусе
    'approved' (см. /approve). Отправляет ровно один товар; для массовой
    отправки фронтенд вызывает это по очереди на каждый одобренный черновик."""
    try:
        return await asyncio.to_thread(v.submit_draft, draft_id, ctx.store_id, ctx.token)
    except v.DraftNotFoundError:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    except v.DraftNotApprovedError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/drafts/{draft_id}")
async def delete_draft(draft_id: str, ctx: StoreContext = Depends(get_store_context)):
    try:
        await asyncio.to_thread(v.delete_draft, draft_id, ctx.store_id)
    except v.DraftNotFoundError:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    return {"deleted": True}
