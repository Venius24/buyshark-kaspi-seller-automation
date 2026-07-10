"""
FastAPI backend — token-first architecture.

Все данные привязаны к токену, не к номеру телефона.
Токен хранится в states/session.json.
"""

import os
import json
import requests
from fastapi import FastAPI, HTTPException, Query
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


# ── Request models ───────────────────────────────────────────

class AuthRequest(BaseModel):
    token: str


class PackOrderRequest(BaseModel):
    order_id: str  # internal Base64 id
    new_status: str = "ACCEPTED_BY_MERCHANT"


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

    _save_session({"token": token})
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
):
    """
    Получить заказы с полной цепочкой (orders → entries → product).
    """
    token = _require_token()
    try:
        orders = KaspiAPI.get_orders_with_products(token, state=state, status=status, limit=limit)
        return {"success": True, "orders": orders, "count": len(orders)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Order status change ─────────────────────────────────────

@app.post("/api/orders/pack")
def pack_order(req: PackOrderRequest):
    """Перевести заказ в новый статус (e.g. ACCEPTED_BY_MERCHANT)."""
    token = _require_token()
    try:
        KaspiAPI.update_order_status(token, req.order_id, req.new_status)
        return {"success": True, "message": f"Заказ {req.order_id} переведён в {req.new_status}."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/orders/pack-all")
def pack_all_orders():
    """Принять все новые заказы (перевести в ACCEPTED_BY_MERCHANT)."""
    token = _require_token()
    try:
        orders = KaspiAPI.get_orders_with_products(token, state="NEW", status="APPROVED_BY_BANK", limit=50)
        packed = 0
        for o in orders:
            try:
                KaspiAPI.update_order_status(token, o["internalId"], "ACCEPTED_BY_MERCHANT")
                packed += 1
            except Exception as e:
                print(f"[API] Pack failed for {o['id']}: {e}")
        return {"success": True, "packed": packed, "message": f"Обработано {packed} заказов."}
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
        except Exception:
            all_products = []

    if not all_products:
        # Fetch from Kaspi API
        try:
            all_products = KaspiAPI.extract_products_from_orders(token)
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


@app.post("/api/products/refresh")
def refresh_products():
    """Принудительно обновить кеш товаров из Kaspi API."""
    token = _require_token()
    products_file = "states/products.json"
    try:
        products = KaspiAPI.extract_products_from_orders(token)
        os.makedirs("states", exist_ok=True)
        with open(products_file, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
        return {"success": True, "count": len(products), "message": f"Обновлено {len(products)} товаров."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
