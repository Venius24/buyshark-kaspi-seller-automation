"""
FastAPI backend — token-first architecture.

Все данные привязаны к токену, не к номеру телефона.
Токен хранится в states/session.json.
"""

import os
import json
import requests
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from backend.kaspi_wrapper import KaspiAPI

app = FastAPI(title="Kaspi SaaS Wrapper API")

# CORS — разрешаем Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статика для ping-pong
app.mount("/ping-pong", StaticFiles(directory="api-ping-pong", html=True), name="ping-pong")

# ── Session storage ──────────────────────────────────────────

SESSION_FILE = "states/session.json"


def _load_session() -> dict:
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_session(data: dict):
    os.makedirs("states", exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_token() -> str:
    """Retrieve saved token or return None."""
    session = _load_session()
    return session.get("token")


def _require_token() -> str:
    """Retrieve saved token or raise 401."""
    token = _get_token()
    if not token:
        raise HTTPException(status_code=401, detail="Токен не найден. Пожалуйста, авторизуйтесь.")
    return token


# ── Product Overrides & Brands ───────────────────────────────

def extract_brand(name: Optional[str]) -> str:
    if not name:
        return "Barkhan"
    name_upper = name.upper()
    for b in ["BARKHAN", "RIDERACE", "CAMPGEAR", "VISION", "FIKS"]:
        if b in name_upper:
            return b.capitalize()
    return "Barkhan"


OVERRIDES_FILE = "states/products_overrides.json"

def _load_overrides() -> dict:
    if os.path.exists(OVERRIDES_FILE):
        try:
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_override(sku: str, price: int, stock: int, brand: Optional[str] = None, status: Optional[str] = None):
    overrides = _load_overrides()
    overrides[sku] = {
        "price_num": price,
        "stock": stock,
        "brand": brand,
        "status": status or ("Активен" if stock > 0 else "Архив")
    }
    os.makedirs("states", exist_ok=True)
    with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)

def _apply_overrides(products: list) -> list:
    overrides = _load_overrides()
    for p in products:
        sku = p.get("sku")
        if "price_num" not in p:
            try:
                price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                p["price_num"] = int(price_str)
            except:
                p["price_num"] = 0
        if "stock" not in p:
            p["stock"] = 10
        if "brand" not in p:
            p["brand"] = extract_brand(p.get("name"))
            
        if sku in overrides:
            ov = overrides[sku]
            if "price_num" in ov:
                p["price_num"] = ov["price_num"]
                p["price"] = f"{ov['price_num']} ₸"
            if "stock" in ov:
                p["stock"] = ov["stock"]
            if "brand" in ov:
                p["brand"] = ov["brand"]
            if "status" in ov:
                p["status"] = ov["status"]
    return products


# ── Request models ───────────────────────────────────────────

class AuthRequest(BaseModel):
    token: str


class PackOrderRequest(BaseModel):
    order_id: str  # internal Base64 id
    order_code: Optional[str] = None
    quantity: Optional[int] = None
    new_status: str = "ACCEPTED_BY_MERCHANT"


class UpdateProductRequest(BaseModel):
    sku: str
    price: int
    stock: int
    brand: Optional[str] = None
    status: Optional[str] = None


# ── Legacy models (kept for future phone-based auth) ─────────
# class PhoneRequest(BaseModel):
#     phone: str
# class SmsRequest(BaseModel):
#     phone: str
#     sms: str


# ── Routes ───────────────────────────────────────────────────

@app.get("/")
def read_root():
    return {"status": "Kaspi SaaS Wrapper API is running", "auth": "token-first"}


# ── Auth ─────────────────────────────────────────────────────

@app.post("/api/auth")
def authenticate(req: AuthRequest):
    """
    Принимает API токен, верифицирует через GET /cities и сохраняет.
    """
    token = req.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Токен не может быть пустым.")

    try:
        valid = KaspiAPI.verify_token(token)
        if not valid:
            raise HTTPException(status_code=401, detail="Kaspi API отклонил токен. Проверьте правильность.")
    except HTTPException:
        raise
    except Exception as e:
        err = str(e)
        # Timeout/network — save anyway (offline mode)
        if "timeout" in err.lower() or "Connection" in err or "NameResolution" in err:
            _save_session({"token": token})
            return {
                "success": True,
                "verified": False,
                "message": f"Токен сохранён (оффлайн-режим: {err})",
            }
        raise HTTPException(status_code=400, detail=f"Ошибка верификации: {err}")

    try:
        merchant_id = KaspiAPI.get_merchant_id(token)
    except Exception:
        merchant_id = "20186986"
    _save_session({"token": token, "merchant_id": merchant_id})
    return {"success": True, "verified": True, "message": "Токен верифицирован и сохранён."}


@app.post("/api/verify-session")
def verify_session():
    """Проверить, есть ли сохранённый токен и валиден ли он."""
    token = _get_token()
    if not token:
        return {"valid": False}

    try:
        valid = KaspiAPI.verify_token(token)
        return {"valid": valid}
    except Exception:
        return {"valid": True}  # offline fallback


@app.post("/api/logout")
def logout():
    """Удалить сохранённый токен."""
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    return {"success": True, "message": "Сессия удалена."}


# ── Orders ───────────────────────────────────────────────────

@app.get("/api/orders")
def get_orders(
    state: str = Query("NEW"),
    status: str = Query("APPROVED_BY_BANK"),
    limit: int = Query(20),
    assembled: Optional[bool] = None,
):
    """
    Получить заказы с полной цепочкой (orders → entries → product).
    """
    token = _require_token()
    try:
        orders = KaspiAPI.get_orders_with_products(token, state=state, status=status, limit=limit, assembled=assembled)
        return {"success": True, "orders": orders, "count": len(orders)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Order status change ─────────────────────────────────────

@app.post("/api/orders/pack")
def pack_order(req: PackOrderRequest):
    """Перевести заказ в новый статус (e.g. ACCEPTED_BY_MERCHANT) или упаковать."""
    token = _require_token()
    try:
        # Check if the order is NEW and needs accepting
        new_orders = KaspiAPI.get_orders_with_products(token, state="KASPI_DELIVERY", status="APPROVED_BY_BANK", limit=50)
        order_item = next((o for o in new_orders if o["internalId"] == req.order_id), None)
        
        if order_item:
            # Transition status to ACCEPTED_BY_MERCHANT
            KaspiAPI.update_order_status(token, req.order_id, "ACCEPTED_BY_MERCHANT")
            return {"success": True, "message": f"Заказ {order_item['id']} принят в обработку."}
            
        # If it's already accepted, it needs cargo assembly
        assembly_orders = KaspiAPI.get_orders_with_products(token, state="KASPI_DELIVERY", status="ACCEPTED_BY_MERCHANT", limit=50, assembled=False)
        order_item = next((o for o in assembly_orders if o["internalId"] == req.order_id), None)
        
        if order_item:
            code = order_item["id"]
            qty = order_item["quantity"]
            KaspiAPI.assemble_cargo(token, code, qty)
            return {"success": True, "message": f"Заказ {code} успешно упакован и готов к передаче."}
            
        raise Exception("Заказ не найден в статусах NEW или ACCEPTED_BY_MERCHANT.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/orders/pack-all")
def pack_all_orders():
    """Принять все новые заказы и упаковать все заказы на сборке."""
    token = _require_token()
    try:
        # 1. Accept all new orders
        new_orders = KaspiAPI.get_orders_with_products(token, state="KASPI_DELIVERY", status="APPROVED_BY_BANK", limit=50)
        accepted_count = 0
        for o in new_orders:
            try:
                KaspiAPI.update_order_status(token, o["internalId"], "ACCEPTED_BY_MERCHANT")
                accepted_count += 1
            except Exception as e:
                print(f"[API] Accept failed for {o['id']}: {e}")
                
        # 2. Assemble all orders currently on assembly
        assembly_orders = KaspiAPI.get_orders_with_products(token, state="KASPI_DELIVERY", status="ACCEPTED_BY_MERCHANT", limit=50, assembled=False)
        non_assembled = assembly_orders
        
        assembled_count = 0
        if non_assembled:
            cargo_items = [{"orderCode": o["id"], "quantity": o["quantity"]} for o in non_assembled]
            try:
                assembled_count = KaspiAPI.assemble_cargos(token, cargo_items)
            except Exception as e:
                print(f"[API] Cargo assembly batch failed: {e}")
                # Fallback to single assembly
                for o in non_assembled:
                    try:
                        KaspiAPI.assemble_cargo(token, o["id"], o["quantity"])
                        assembled_count += 1
                    except Exception as ex:
                        print(f"[API] Cargo assembly failed for {o['id']}: {ex}")
                        
        msg = f"Принято новых заказов: {accepted_count}. Упаковано заказов: {assembled_count}."
        return {"success": True, "packed": accepted_count + assembled_count, "message": msg}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Products ─────────────────────────────────────────────────

@app.get("/api/products")
def get_products(
    page: int = Query(1),
    page_size: int = Query(20),
    search: str = Query(""),
    status: str = Query("active"),
):
    """
    Получить список товаров из истории заказов (3-step chain → deduplicate).
    Кэшируется в states/products.json.
    """
    token = _require_token()
    products_file = "states/products.json"

    # Try to load cached products, or fetch fresh
    all_products = []
    if os.path.exists(products_file):
        try:
            with open(products_file, "r", encoding="utf-8") as f:
                all_products = json.load(f)
                all_products = _apply_overrides(all_products)
        except Exception:
            all_products = []

    if not all_products:
        # Fetch from Kaspi API
        try:
            all_products = KaspiAPI.extract_products_from_orders(token)
            all_products = _apply_overrides(all_products)
            os.makedirs("states", exist_ok=True)
            with open(products_file, "w", encoding="utf-8") as f:
                json.dump(all_products, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Не удалось получить товары: {e}")

    # Filter by status
    target_status = "Активен" if status == "active" else "Архив"
    filtered = [p for p in all_products if p.get("status") == target_status]

    # Filter by search
    if search:
        q = search.lower()
        filtered = [p for p in filtered if q in p.get("name", "").lower() or q in p.get("sku", "").lower()]

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


@app.post("/api/products/update")
def update_product(req: UpdateProductRequest):
    """Обновить цену и остаток товара (сохранить в overrides)."""
    _require_token()
    products_file = "states/products.json"
    if not os.path.exists(products_file):
        raise HTTPException(status_code=404, detail="Список товаров не найден. Загрузите товары сначала.")
        
    try:
        # Save override
        _save_override(req.sku, req.price, req.stock, req.brand, req.status)
        
        # Load and update products list
        with open(products_file, "r", encoding="utf-8") as f:
            products = json.load(f)
            
        updated = False
        for p in products:
            if p.get("sku") == req.sku:
                p["price_num"] = req.price
                p["price"] = f"{req.price} ₸"
                p["stock"] = req.stock
                if req.brand:
                    p["brand"] = req.brand
                p["status"] = req.status or ("Активен" if req.stock > 0 else "Архив")
                updated = True
                break
                
        if not updated:
            raise HTTPException(status_code=404, detail=f"Товар с SKU {req.sku} не найден.")
            
        with open(products_file, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
            
        return {"success": True, "message": f"Товар {req.sku} успешно обновлен."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/products/refresh")
def refresh_products():
    """Принудительно обновить кеш товаров из Kaspi API."""
    token = _require_token()
    products_file = "states/products.json"
    try:
        products = KaspiAPI.extract_products_from_orders(token)
        products = _apply_overrides(products)
        os.makedirs("states", exist_ok=True)
        with open(products_file, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
        return {"success": True, "count": len(products), "message": f"Обновлено {len(products)} товаров."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/feed")
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
            
    from xml.etree.ElementTree import Element, SubElement, tostring
    import datetime
    
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    
    root_el = Element("kaspi_catalog", {
        "date": now_str,
        "xmlns": "http://chat.carstyle.kz/kaspi_catalog",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"
    })
    
    company_el = SubElement(root_el, "company")
    company_el.text = "ИП Твой Мерчант"
    
    merchant_el = SubElement(root_el, "merchantid")
    merchant_el.text = str(merchant_id)
    
    offers_el = SubElement(root_el, "offers")
    
    for p in products:
        sku = p.get("sku")
        if not sku:
            continue
            
        name = p.get("name") or f"Товар {sku}"
        brand = p.get("brand") or extract_brand(name)
        
        price_num = p.get("price_num")
        if price_num is None:
            try:
                price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                price_num = int(price_str)
            except:
                price_num = 0
                
        stock = p.get("stock", 10)
        status = p.get("status", "Активен")
        
        available = "yes" if stock > 0 and status == "Активен" else "no"
        
        offer_el = SubElement(offers_el, "offer", {"sku": str(sku)})
        model_el = SubElement(offer_el, "model")
        model_el.text = str(name)
        
        brand_el = SubElement(offer_el, "brand")
        brand_el.text = str(brand)
        
        avail_parent = SubElement(offer_el, "availabilities")
        SubElement(avail_parent, "availability", {
            "available": available,
            "storeId": f"{merchant_id}_001",
            "stock": str(stock)
        })
        
        price_el = SubElement(offer_el, "price")
        price_el.text = str(price_num)
        
    xml_str = tostring(root_el, encoding="utf-8")
    return Response(content=b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_str, media_type="application/xml")


# ── Transfers ────────────────────────────────────────────────

@app.get("/api/transfers")
def get_transfers(filter: str = Query("ALL")):
    """Получить список передач (SIGN_REQUIRED orders)."""
    token = _require_token()
    try:
        transfers = KaspiAPI.get_transfers(token, delivery_filter=filter)
        return {"success": True, "transfers": transfers, "count": len(transfers)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Cities ───────────────────────────────────────────────────

@app.get("/api/cities")
def get_cities():
    """Получить справочник городов."""
    token = _require_token()
    try:
        cities = KaspiAPI.get_cities(token)
        return {"success": True, "cities": cities}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Legacy endpoints (commented out, kept for future use) ────
# @app.post("/api/start-login")
# def start_login(req: PhoneRequest):
#     return {"success": True, "message": "Store identifier received."}
#
# @app.post("/api/submit-sms")
# def submit_sms(req: SmsRequest):
#     token = req.sms.strip()
#     save_token(req.phone, token)
#     return {"success": True}

@app.post("/api/close")
def close_session():
    return {"success": True}
