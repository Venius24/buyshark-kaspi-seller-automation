import os
import json
from fastapi import APIRouter, HTTPException, Query, Response
from backend.kaspi_wrapper import KaspiAPI
from backend.models import UpdateProductRequest
from backend.state_helpers import _require_token, _load_session, _save_override, _apply_overrides
from backend.dumper import generate_xml_feed

router = APIRouter(prefix="/api", tags=["products"])

@router.get("/products")
def get_products(
    page: int = Query(1),
    page_size: int = Query(20),
    search: str = Query(""),
    status: str = Query("active"),
    dumping_only: bool = Query(False),
    dumping_status_filter: str = Query(""),
):
    """Получить список товаров из кэша или Kaspi API с конфигурациями и статусами демпинга."""
    token = _require_token()
    products_file = "states/products.json"

    all_products = []
    if os.path.exists(products_file):
        try:
            with open(products_file, "r", encoding="utf-8") as f:
                all_products = json.load(f)
                all_products = _apply_overrides(all_products)
        except Exception:
            all_products = []

    if not all_products:
        try:
            all_products = KaspiAPI.sync_merchant_assortment(token)
            all_products = _apply_overrides(all_products)
            os.makedirs("states", exist_ok=True)
            with open(products_file, "w", encoding="utf-8") as f:
                json.dump(all_products, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Не удалось получить товары: {e}")

    # Загружаем конфигурации демпинга и обогащаем товары
    from backend.state_helpers import _load_dumping_configs
    dumping_configs = _load_dumping_configs()

    for p in all_products:
        sku = p.get("sku")
        cfg = dumping_configs.get(sku)
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
def update_product(req: UpdateProductRequest):
    """Обновить цену и остаток товара (сохранить в overrides и применить в Kaspi MC)."""
    token = _require_token()
    products_file = "states/products.json"
    if not os.path.exists(products_file):
        raise HTTPException(status_code=404, detail="Список товаров не найден. Загрузите товары сначала.")
        
    try:
        _save_override(req.sku, req.price, req.stock, req.brand, req.status)
        
        with open(products_file, "r", encoding="utf-8") as f:
            products = json.load(f)
            
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
            
        with open(products_file, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
            
        # Моментальный push в Kaspi MC
        from backend.dumper.executor import _load_mc_cookies, RateLimitExecutorWorker
        from backend.dumper.config import discover_merchant_id, _decode_base64_if_needed
        
        cookies = _load_mc_cookies()
        mc_info = ""
        if cookies and "mc-session" in cookies and "mc-sid" in cookies:
            merchant_id = discover_merchant_id(token) or "20186986"
            master_sku = target_p.get("kaspiSku") or target_p.get("sku")
            decoded_master = _decode_base64_if_needed(master_sku)
            
            store_full_id = target_p.get("storeId") or f"{merchant_id}_001"
            store_suffix = store_full_id.split("_")[1] if "_" in store_full_id else "001"
            
            success, val = RateLimitExecutorWorker._update_kaspi_price_sync(
                merchant_uid=merchant_id,
                cookies=cookies,
                sku=target_p.get("merchantSku") or req.sku,
                master_sku=decoded_master,
                model_name=target_p.get("name", "Товар"),
                new_price=int(req.price),
                stock=int(req.stock),
                store_id_suffix=store_suffix,
                city_id="750000000"
            )
            if success:
                mc_info = f" и успешно отправлен в Kaspi MC (ID: {val})"
            else:
                mc_info = f" (локально, ошибка MC: {val})"
        else:
            mc_info = " (локально, куки MC отсутствуют)"
            
        return {"success": True, "message": f"Товар {req.sku} успешно обновлен{mc_info}."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/products/refresh")
def refresh_products():
    """Принудительно обновить кеш товаров из Kaspi API."""
    token = _require_token()
    products_file = "states/products.json"
    try:
        products = KaspiAPI.sync_merchant_assortment(token)
        products = _apply_overrides(products)
        os.makedirs("states", exist_ok=True)
        with open(products_file, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
        return {"success": True, "count": len(products), "message": f"Обновлено {len(products)} товаров."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/feed")
def get_xml_feed():
    """Сгенерировать XML-фид для Kaspi Catalog Sync."""
    session = _load_session()
    merchant_id = session.get("merchant_id", "20186986")
    
    products_file = "states/products.json"
    products = []
    if os.path.exists(products_file):
        try:
            with open(products_file, "r", encoding="utf-8") as f:
                products = json.load(f)
        except Exception:
            pass
            
    xml_data = generate_xml_feed(merchant_id, products)
    return Response(content=xml_data, media_type="application/xml")
