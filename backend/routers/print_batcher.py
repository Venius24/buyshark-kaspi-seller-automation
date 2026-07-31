import asyncio
import functools

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from backend.services.print_batcher_service import (
    process_zip_or_pdfs,
    get_batch_file_path,
    reset_printed_history,
    load_printed_history
)
from backend.security.store_context import StoreContext, get_store_context

router = APIRouter(prefix="/api/print-batcher", tags=["Print Batcher Engine"])


@router.post("/process")
async def process_print_batch(
    file: UploadFile = File(...),
    sort_preset: str = Form("filename"),            # 'filename' | 'order_id' | 'original'
    auto_rotate: bool = Form(True),
    express_mode: str = Form("separate"),            # 'separate' | 'exclude' | 'ignore'
    deduplicate_history: bool = Form(True),
    force_reprint: bool = Form(False),
    output_layout: str = Form("auto"),              # 'auto' | 'a4_4' | 'a4_8' | 'a4_9' | 'a4_1' | 'thermal_75_120' | 'thermal_100_150' | 'just_merge'
    ctx: StoreContext = Depends(get_store_context),
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

    try:
        content = await file.read()
        # process_zip_or_pdfs is a synchronous, CPU-heavy PyMuPDF pipeline
        # (page-by-page parsing/rendering, PDF re-composition) — running it
        # inline on the event loop froze the whole single-worker app for
        # the duration of every print job.
        manifest = await asyncio.to_thread(
            functools.partial(
                process_zip_or_pdfs,
                file_bytes=content,
                original_filename=file.filename,
                sort_preset=sort_preset,
                auto_rotate=auto_rotate,
                express_mode=express_mode,
                deduplicate_history=deduplicate_history,
                force_reprint=force_reprint,
                output_layout=output_layout,
                store_id=ctx.store_id,
            )
        )
        return JSONResponse(content=manifest)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка обработки печати: {str(e)}")


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
