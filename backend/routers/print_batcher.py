import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from backend.services.print_batcher_service import (
    process_zip_or_pdfs,
    get_batch_file_path,
    reset_printed_history,
    load_printed_history,
    detect_article_prefixes,
)
from backend.security.store_context import StoreContext, get_store_context, get_store_context_billed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/print-batcher", tags=["Print Batcher Engine"])

# Склейка крупной партии — это десятки секунд чистого CPU (1200 накладных ≈ 55с
# на проде). Держать на это открытый HTTP-запрос нельзя: перед сервисом стоит
# Cloudflare с жёстким лимитом 100с, и он обрывает соединение, отдавая в
# браузер HTML-страницу ошибки — фронт спотыкался об неё как об
# "Unexpected token '<'". Поэтому обработка уходит в фоновую задачу, а клиент
# опрашивает её статус короткими запросами.
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL_SECONDS = 3600


def _prune_jobs() -> None:
    """Задачи живут час — дальше манифест всё равно не нужен, а файлы батчей чистятся отдельно."""
    deadline = time.time() - _JOB_TTL_SECONDS
    for job_id in [j for j, v in _JOBS.items() if v["updated_at"] < deadline]:
        _JOBS.pop(job_id, None)


def _set_job(job_id: str, **fields: Any) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def _make_progress_cb(job_id: str):
    def _cb(stage: str, done: int, total: int) -> None:
        _set_job(job_id, progress={"stage": stage, "done": done, "total": total})
    return _cb


def _run_job(job_id: str, kwargs: Dict[str, Any]) -> None:
    """
    Выполняется в отдельном потоке, а не задачей event loop: конвейер
    синхронный и CPU-тяжёлый, и он обязан пережить завершение HTTP-запроса,
    который его породил.
    """
    try:
        manifest = process_zip_or_pdfs(**kwargs)
        _set_job(job_id, status="done", manifest=manifest)
    except ValueError as ve:
        _set_job(job_id, status="error", detail=str(ve))
    except Exception as e:
        logger.exception("Print Batcher: фоновая склейка упала")
        _set_job(job_id, status="error", detail=f"Ошибка обработки печати: {e}")


@router.post("/process")
async def process_print_batch(
    file: UploadFile = File(...),
    sort_preset: str = Form("filename"),            # 'filename' | 'order_id' | 'original' | 'product_name' | 'master_group'
    auto_rotate: bool = Form(True),
    express_mode: str = Form("separate"),            # 'separate' | 'exclude' | 'ignore'
    deduplicate_history: bool = Form(True),
    force_reprint: bool = Form(False),
    output_layout: str = Form("auto"),              # 'auto' | 'a4_4' | 'a4_8' | 'a4_9' | 'a4_1' | 'thermal_75_120' | 'thermal_100_150' | 'just_merge'
    ctx: StoreContext = Depends(get_store_context_billed),
):
    """
    Принимает ZIP-архив или PDF-файл, выполняет суточную дедупликацию по истории печати,
    сортирует и компонует накладные по выбранному формату мульти-раскладки.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Файл не передан")

    filename = file.filename.lower()
    if not (filename.endswith(".zip") or filename.endswith(".pdf")):
        raise HTTPException(status_code=400, detail="Формат файла не поддерживается. Загрузите .zip или .pdf")

    content = await file.read()

    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        _prune_jobs()
        _JOBS[job_id] = {
            "store_id": ctx.store_id,
            "status": "processing",
            "manifest": None,
            "detail": None,
            "progress": None,
            "updated_at": time.time(),
        }

    # process_zip_or_pdfs — синхронный CPU-тяжёлый конвейер PyMuPDF: в потоке,
    # чтобы не морозить единственный воркер uvicorn на всё время склейки.
    threading.Thread(target=_run_job, daemon=True, name=f"print-batch-{job_id[:8]}", args=(job_id, dict(
        file_bytes=content,
        original_filename=file.filename,
        sort_preset=sort_preset,
        auto_rotate=auto_rotate,
        express_mode=express_mode,
        # Галочка в UI — про то, отсеивать ли повторы В ЭТОМ прогоне. Журнал
        # напечатанного ведём всегда: снятая галочка означала «напечатать всё»,
        # а не «забыть, что мы это печатали», иначе следующая пачка не смогла
        # бы отличить в ней новое от повторов.
        deduplicate_history=True,
        filter_duplicates=deduplicate_history,
        force_reprint=force_reprint,
        output_layout=output_layout,
        store_id=ctx.store_id,
        # Крупные категории — настройка магазина, а не параметр запроса:
        # один и тот же пресет обязан действовать и в кабинете, и в боте.
        store_categories=_load_store_categories(ctx.store_id),
        progress_cb=_make_progress_cb(job_id),
    ))).start()

    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "processing"})


@router.get("/process-status/{job_id}")
async def get_process_status(job_id: str, ctx: StoreContext = Depends(get_store_context)):
    """
    Статус фоновой склейки. Пока status == "processing", фронт продолжает
    опрашивать; "done" отдаёт манифест, "error" — текст ошибки.
    """
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        snapshot = dict(job) if job else None

    if snapshot is None:
        # Перезапуск сервиса теряет задачи — честно говорим об этом, иначе
        # фронт будет опрашивать несуществующий job до бесконечности.
        raise HTTPException(status_code=404, detail="Задача не найдена или устарела — запустите обработку заново")
    if snapshot["store_id"] != ctx.store_id:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    return {
        "status": snapshot["status"],
        "progress": snapshot["progress"],
        "manifest": snapshot["manifest"],
        "detail": snapshot["detail"],
    }


def _load_store_categories(store_id: str) -> Dict[str, Any]:
    from backend.chatbot import config as chatbot_config
    return (chatbot_config.load_print_settings(store_id) or {}).get("store_categories") or {}


@router.get("/settings")
async def get_print_settings(ctx: StoreContext = Depends(get_store_context)):
    """
    Настройки печати магазина, включая пресет крупных категорий.

    Кабинет раньше хранил их только в localStorage браузера, а бот — в БД, и
    один и тот же магазин печатал по-разному в зависимости от того, откуда
    запустили. Общий источник — StorePrintSettingsDB.
    """
    from backend.chatbot import config as chatbot_config
    settings = await asyncio.to_thread(chatbot_config.load_print_settings, ctx.store_id)
    return settings


@router.put("/settings")
async def put_print_settings(payload: Dict[str, Any], ctx: StoreContext = Depends(get_store_context_billed)):
    """Сохранить настройки печати магазина (частичное обновление)."""
    from backend.chatbot import config as chatbot_config

    allowed = {
        "output_layout", "express_mode", "sort_preset", "deduplicate_history",
        "auto_rotate", "dup_handling_mode", "store_categories",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(status_code=400, detail=f"Неизвестные настройки: {', '.join(sorted(unknown))}")

    cats = payload.get("store_categories")
    if cats is not None:
        if not isinstance(cats, dict):
            raise HTTPException(status_code=400, detail="store_categories должен быть объектом")
        items = cats.get("categories") or []
        if not isinstance(items, list):
            raise HTTPException(status_code=400, detail="store_categories.categories должен быть списком")
        seen = set()
        for item in items:
            prefix = str((item or {}).get("prefix") or "").strip().upper()
            if not prefix:
                raise HTTPException(status_code=400, detail="У категории пустой префикс артикула")
            if prefix in seen:
                raise HTTPException(status_code=400, detail=f"Префикс {prefix} указан дважды")
            seen.add(prefix)

    saved = await asyncio.to_thread(chatbot_config.save_print_settings, payload, ctx.store_id)
    return saved


@router.get("/detected-prefixes")
async def get_detected_prefixes(ctx: StoreContext = Depends(get_store_context)):
    """
    Префиксы артикулов, реально встречающиеся в каталоге магазина.

    Владельцу остаётся только проставить человеческие названия — вписывать
    буквы руками и следить за появлением новых не нужно.
    """
    from backend.state_helpers import _load_products
    products = await asyncio.to_thread(_load_products, ctx.store_id)
    return {"prefixes": detect_article_prefixes(products or [])}


@router.post("/reset-history")
async def reset_history(ctx: StoreContext = Depends(get_store_context)):
    """Сбросить суточную историю напечатанных заказов"""
    try:
        await asyncio.to_thread(reset_printed_history, ctx.store_id)
        return {"status": "ok", "message": "История напечатанных заказов за сегодня успешно сброшена"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при сбросе истории: {str(e)}")


@router.get("/history-stats")
async def get_history_stats(ctx: StoreContext = Depends(get_store_context)):
    """Получить количество напечатанных заказов в истории за сегодня"""
    history_keys = await asyncio.to_thread(load_printed_history, ctx.store_id)
    return {"printed_count": len(history_keys)}


@router.get("/download/{batch_id}/{category}")
async def download_print_batch(batch_id: str, category: str, ctx: StoreContext = Depends(get_store_context)):
    """Скачать скомпонованный PDF-файл конкретной категории"""
    file_path = get_batch_file_path(batch_id, category)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл батча не найден или истёк срок хранения")

    download_name = f"PRINT_BATCH_{category}.pdf"
    return FileResponse(
        path=str(file_path),
        filename=download_name,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'}
    )


@router.get("/file-preview/{batch_id}/{category}")
async def preview_print_batch(batch_id: str, category: str, ctx: StoreContext = Depends(get_store_context)):
    """Предпросмотр / Прямая печать из браузера (Content-Disposition: inline)"""
    file_path = get_batch_file_path(batch_id, category)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл батча не найден")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline'}
    )
