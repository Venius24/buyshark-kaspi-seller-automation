import datetime
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from backend.kaspi_wrapper import KaspiAPI
from backend.models import UpdateProductRequest
from backend.state_helpers import _save_override, _apply_overrides, _load_products, _save_products, _purge_stale_overrides
from backend.security.store_context import StoreContext, get_store_context
from backend.dumper import generate_xml_feed

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["products"])

@router.get("/products")
def get_products(
    page: int = Query(1),
    page_size: int = Query(20),
    search: str = Query(""),
    status: str = Query("active"),
    dumping_only: bool = Query(False),
    dumping_status_filter: str = Query(""),
    ctx: StoreContext = Depends(get_store_context),
):
    """Получить список товаров из кэша или Kaspi API с конфигурациями и статусами демпинга."""
    token = ctx.token

    all_products = _load_products(ctx.store_id)
    # Если кэш содержит тестовые фейковые товары SKU-001 — отбрасываем
    if any(p.get("sku") in ["SKU-001", "SKU-002"] for p in all_products):
        all_products = []
    else:
        all_products = _apply_overrides(all_products, store_id=ctx.store_id)

    if not all_products:
        try:
            all_products = KaspiAPI.sync_merchant_assortment(token, store_id=ctx.store_id)
            all_products = _apply_overrides(all_products, store_id=ctx.store_id)
            _save_products(all_products, ctx.store_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Не удалось получить товары: {e}")

    # Загружаем конфигурации демпинга и обогащаем товары
    from backend.state_helpers import _load_dumping_configs, dumping_config_for_product
    dumping_configs = _load_dumping_configs(store_id=ctx.store_id)

    for p in all_products:
        # Матч сперва по merchantSku, потом по sku (dumping_config_for_product).
        # Раньше здесь был матч ТОЛЬКО по sku, а конфиги пишутся по merchantSku:
        # на проде 374 из 377 конфигов были невидимы для этой ручки — товары
        # показывались как "демпинг выключен", фильтр «только с демпингом» отдавал
        # пустоту, и успешный импорт из PriceFeed выглядел как несостоявшийся.
        cfg = dumping_config_for_product(p, dumping_configs)
        p["dumping_config"] = cfg
        if not cfg or not cfg.get("enabled"):
            p["dumping_status"] = "OFF"
        elif "dumping_status" not in p:
            p["dumping_status"] = "WINNING_1ST"

    if status and status != "all":
        target_status = "Активен" if status == "active" else ("Архив" if status == "archive" else status)
        filtered = [p for p in all_products if p.get("status") == target_status]
    else:
        filtered = list(all_products)


    # Фильтр только активных товаров в авто-демпинге
    if dumping_only:
        filtered = [p for p in filtered if p.get("dumping_config") and p["dumping_config"].get("enabled")]

    # Фильтр по рабочему статусу демпинга
    if dumping_status_filter:
        q_status = dumping_status_filter.strip().upper()
        filtered = [p for p in filtered if str(p.get("dumping_status", "")).upper() == q_status]

    if search:
        q = search.lower()
        filtered_list = []
        for p in filtered:
            match_fields = [
                p.get("name"),
                p.get("sku"),
                p.get("merchantSku"),
                p.get("kaspiSku"),
                p.get("brand"),
                p.get("id")
            ]
            if any(q in str(field).lower() for field in match_fields if field is not None):
                filtered_list.append(p)
        filtered = filtered_list

    total_count = len(filtered)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    start = (page - 1) * page_size
    sliced = filtered[start : start + page_size]

    return {
        "success": True,
        "products": sliced,
        "total_count": total_count,
        "total_pages": total_pages,
    }

@router.post("/products/update")
def update_product(req: UpdateProductRequest, ctx: StoreContext = Depends(get_store_context)):
    """Обновить цену и остаток товара (сохранить в overrides и применить в Kaspi MC)."""
    token = ctx.token
    products = _load_products(ctx.store_id)
    if not products:
        raise HTTPException(status_code=404, detail="Список товаров не найден. Загрузите товары сначала.")

    try:
        _save_override(req.sku, req.price, req.stock, req.brand, req.status, store_id=ctx.store_id)

        updated = False
        target_p = None
        for p in products:
            if p.get("sku") == req.sku:
                p["price_num"] = req.price
                p["price"] = f"{req.price} ₸"
                p["stock"] = req.stock
                if req.brand:
                    p["brand"] = req.brand
                p["status"] = req.status or ("Активен" if req.stock > 0 else "Архив")
                target_p = p
                updated = True
                break

        if not updated:
            raise HTTPException(status_code=404, detail=f"Товар с SKU {req.sku} не найден.")

        _save_products(products, ctx.store_id)

        # Моментальный push в Kaspi MC
        from backend.dumper.executor import _load_mc_cookies, RateLimitExecutorWorker
        from backend.dumper.config import discover_merchant_id, _decode_base64_if_needed

        cookies = _load_mc_cookies(store_id=ctx.store_id)
        mc_info = ""
        merchant_id = discover_merchant_id(token) if (cookies and "mc-session" in cookies and "mc-sid" in cookies) else None
        if merchant_id:
            master_sku = target_p.get("kaspiSku") or target_p.get("sku")
            decoded_master = _decode_base64_if_needed(master_sku)

            store_full_id = target_p.get("storeId") or f"{merchant_id}_001"
            store_suffix = store_full_id.split("_")[1] if "_" in store_full_id else "001"

            success, val = RateLimitExecutorWorker._update_kaspi_price_sync(
                merchant_uid=merchant_id,
                sku=target_p.get("merchantSku") or req.sku,
                master_sku=decoded_master,
                model_name=target_p.get("name", "Товар"),
                new_price=int(req.price),
                stock=int(req.stock),
                store_id_suffix=store_suffix,
                city_id="750000000",
                internal_store_id=ctx.store_id
            )
            if success:
                mc_info = f" и успешно отправлен в Kaspi MC (ID: {val})"
            else:
                mc_info = f" (локально, ошибка MC: {val})"
        elif cookies and "mc-session" in cookies and "mc-sid" in cookies:
            # НЕ подставляем чужой merchant_id (раньше был хардкод "20186986" —
            # реальный ID Barkhan, из-за чего push для другого магазина уходил
            # под его личностью и ловил 403 от Kaspi).
            mc_info = " (локально, не удалось определить Merchant ID)"
        else:
            mc_info = " (локально, куки MC отсутствуют)"

        return {"success": True, "message": f"Товар {req.sku} успешно обновлен{mc_info}."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/products/refresh")
def refresh_products(ctx: StoreContext = Depends(get_store_context)):
    """Принудительно обновить кеш товаров из Kaspi API."""
    try:
        fetched_at = datetime.datetime.utcnow()
        products = KaspiAPI.sync_merchant_assortment(ctx.token, store_id=ctx.store_id)
        # Тот же барьер свежести, что и в products_sync_worker: только что
        # выкачанную из Kaspi цену не перекрываем вчерашним оверрайдом.
        products = _apply_overrides(products, store_id=ctx.store_id, fresh_from_kaspi_at=fetched_at)
        _save_products(products, ctx.store_id)
        _purge_stale_overrides(ctx.store_id, fetched_at)
        return {"success": True, "count": len(products), "message": f"Обновлено {len(products)} товаров."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/feed")
def get_xml_feed(ctx: StoreContext = Depends(get_store_context)):
    """Сгенерировать XML-фид для Kaspi Catalog Sync (магазина текущего пользователя).

    Раньше был вообще без авторизации (читал states/session.json напрямую) —
    теперь, как и остальные эндпоинты этого роутера, резолвит контекст через
    dual-auth (см. security/store_context.py::get_store_context).
    """
    products = _load_products(ctx.store_id)
    xml_data = generate_xml_feed(ctx.merchant_id, products)
    return Response(content=xml_data, media_type="application/xml")
