"""
FastAPI backend — token-first architecture.

Все данные привязаны к токену, не к номеру телефона.
Токен хранится в states/session.json.
"""

import os
import json
import requests
from typing import Optional, List
from dataclasses import dataclass
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from backend.kaspi_wrapper import KaspiAPI
import queue
import threading
import random
from playwright.sync_api import sync_playwright

# ── Dumping Engine ───────────────────────────────────────────

@dataclass
class ProductDumpingConfig:
    sku: str
    my_current_price: float
    min_price: float
    max_price: float
    dump_step: float = 10.0


class DumpingEngine:
    @staticmethod
    def calculate_new_price(
        config: ProductDumpingConfig, competitor_prices: List[float]
    ) -> float:
        # 1. Filter out our own current price
        other_prices = [
            p for p in competitor_prices if abs(p - config.my_current_price) > 0.01
        ]

        # If no other sellers, return max_price
        if not other_prices:
            return config.max_price

        # 2. Min competitor price
        min_competitor_price = min(other_prices)

        # 3. Calculate target price
        target_price = min_competitor_price - config.dump_step

        # 4. Check min_price limit
        if target_price < config.min_price:
            return config.min_price

        # 5. Check if we already have the best price
        if config.my_current_price < min_competitor_price:
            # If gap is too large, pull price up
            if (min_competitor_price - config.my_current_price) > (
                config.dump_step * 2
            ):
                return min_competitor_price - config.dump_step
            return config.my_current_price

        return target_price


DUMPING_CONFIGS_FILE = "states/dumping_configs.json"


def _load_dumping_configs() -> dict:
    if os.path.exists(DUMPING_CONFIGS_FILE):
        try:
            with open(DUMPING_CONFIGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_dumping_configs(configs: dict):
    os.makedirs("states", exist_ok=True)
    with open(DUMPING_CONFIGS_FILE, "w", encoding="utf-8") as f:
        json.dump(configs, f, ensure_ascii=False, indent=2)


def fetch_competitor_prices(sku: str, current_price: float) -> List[float]:
    base = current_price if current_price > 100 else 2000.0
    if random.random() < 0.8:
        c1 = round(base - random.randint(10, 80))
        c2 = round(base + random.randint(20, 100))
        c3 = round(base - random.randint(20, 50))
        return [float(current_price), float(c1), float(c2), float(c3)]
    return [float(current_price)]


class Command:
    START_LOGIN = "start_login"
    SUBMIT_SMS = "submit_sms"
    VERIFY_SESSION = "verify_session"
    CLEANUP = "cleanup"


class PlaywrightWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.cmd_queue = queue.Queue()
        self.result_queue = queue.Queue()

    def run(self):
        with sync_playwright() as playwright:
            browser = None
            context = None
            page = None
            
            while True:
                cmd, data = self.cmd_queue.get()
                try:
                    if cmd == Command.START_LOGIN:
                        phone = data
                        if browser:
                            try:
                                browser.close()
                            except:
                                pass
                            browser, context, page = None, None, None
                        
                        browser = playwright.chromium.launch(headless=False)
                        context = browser.new_context()
                        page = context.new_page()
                        
                        page.goto("https://idmc.shop.kaspi.kz/login")
                        page.get_by_role("tab", name="Телефон").click()
                        page.get_by_role("textbox", name="+7 ( ) - -").click()
                        page.get_by_role("textbox", name="+7 ( ) - -").fill(phone)
                        page.get_by_role("button", name="Продолжить").click()
                        
                        page.wait_for_timeout(1000)
                        self.result_queue.put((True, "SMS sent to phone"))
                        
                    elif cmd == Command.SUBMIT_SMS:
                        sms = data
                        if not page:
                            raise Exception("Сессия авторизации не начата.")
                        
                        page.get_by_role("textbox", name="Введите код").click()
                        page.get_by_role("textbox", name="Введите код").fill(sms)
                        page.get_by_role("button", name="Продолжить").click()
                        
                        # Ожидаем появление кук mc-session и mc-sid
                        success_cookies = False
                        for _ in range(30):  # до 15 секунд
                            cookies = context.cookies()
                            names = [c["name"] for c in cookies]
                            if "mc-session" in names and "mc-sid" in names:
                                success_cookies = True
                                break
                            page.wait_for_timeout(500)
                        
                        if not success_cookies:
                            page.wait_for_timeout(2000)  # дополнительный таймаут-бекдор
                        
                        if "login" in page.url:
                            raise Exception("Вход не удался. Пожалуйста, проверьте код из SMS.")
                        
                        context.storage_state(path="state.json")
                        
                        browser.close()
                        browser, context, page = None, None, None
                        
                        self.result_queue.put((True, "Success"))
                        
                    elif cmd == Command.VERIFY_SESSION:
                        if os.path.exists("state.json"):
                            temp_browser = playwright.chromium.launch(headless=True)
                            temp_context = temp_browser.new_context(storage_state="state.json")
                            temp_page = temp_context.new_page()
                            temp_page.goto("https://kaspi.kz/mc/#/orders?status=NEW")
                            temp_page.wait_for_timeout(3000)
                            
                            is_valid = "login" not in temp_page.url
                            if is_valid:
                                # Сохраняем обновленный стейт (куки mc-session, mc-sid и т.д.)
                                temp_context.storage_state(path="state.json")
                            temp_browser.close()
                            self.result_queue.put((True, is_valid))
                        else:
                            self.result_queue.put((True, False))
                            
                    elif cmd == Command.CLEANUP:
                        if browser:
                            try:
                                browser.close()
                            except:
                                pass
                            browser, context, page = None, None, None
                        self.result_queue.put((True, "Cleaned up"))
                        
                except Exception as e:
                    self.result_queue.put((False, str(e)))

    def send_cmd(self, cmd, data=None):
        self.cmd_queue.put((cmd, data))
        return self.result_queue.get()


worker = PlaywrightWorker()
worker.start()

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


class PhoneRequest(BaseModel):
    phone: str


class SmsRequest(BaseModel):
    sms: str


class DumpingConfigRequest(BaseModel):
    sku: str
    min_price: float
    max_price: float
    dump_step: Optional[float] = 10.0
    enabled: bool
    city_code: Optional[str] = "750000000"


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
        new_orders = KaspiAPI.get_orders_with_products(token, state="NEW", status="APPROVED_BY_BANK", limit=50)
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
        new_orders = KaspiAPI.get_orders_with_products(token, state="NEW", status="APPROVED_BY_BANK", limit=50)
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
            import concurrent.futures
            
            # Запускаем сборку параллельно в 3 потока, так как пакетный метод Kaspi (assemble_cargos) не работает
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    executor.submit(KaspiAPI.assemble_cargo, token, o["id"], o["quantity"]): o
                    for o in non_assembled
                }
                for future in concurrent.futures.as_completed(futures):
                    order = futures[future]
                    try:
                        future.result()
                        assembled_count += 1
                    except Exception as ex:
                        print(f"[API] Cargo assembly failed for order {order['id']}: {ex}")
                        
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
            all_products = KaspiAPI.sync_merchant_assortment(token)
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
        filtered = [p for p in filtered if q in (p.get("name") or "").lower() or q in (p.get("sku") or "").lower()]

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
        products = KaspiAPI.sync_merchant_assortment(token)
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
            
    from backend.price_dumper import generate_xml_feed
    xml_data = generate_xml_feed(merchant_id, products)
    return Response(content=xml_data, media_type="application/xml")


# ── Price Dumping ────────────────────────────────────────────

@app.get("/api/dumping/configs")
def get_dumping_configs():
    """Получить все конфигурации авто-демпинга."""
    configs = _load_dumping_configs()
    return {"success": True, "configs": configs}


@app.post("/api/dumping/config")
def save_dumping_config(req: DumpingConfigRequest):
    """Сохранить или обновить конфигурацию демпинга для SKU."""
    configs = _load_dumping_configs()
    configs[req.sku] = {
        "min_price": req.min_price,
        "max_price": req.max_price,
        "dump_step": req.dump_step,
        "enabled": req.enabled,
        "city_code": req.city_code
    }
    _save_dumping_configs(configs)
    return {"success": True, "message": f"Конфигурация демпинга для {req.sku} сохранена."}


@app.post("/api/dumping/run")
async def run_price_dumping():
    """Запустить расчет авто-демпинга для включенных товаров."""
    configs = _load_dumping_configs()
    products_file = "states/products.json"
    
    if not os.path.exists(products_file):
        raise HTTPException(status_code=404, detail="Список товаров пуст. Загрузите товары.")
        
    try:
        with open(products_file, "r", encoding="utf-8") as f:
            products = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения товаров: {e}")
        
    token = _require_token()
    
    from backend.price_dumper import run_price_dumping_job
    
    res = await run_price_dumping_job(
        token=token,
        configs=configs,
        products=products,
        save_override_func=_save_override
    )
    
    if res.get("updated_count", 0) > 0:
        try:
            with open(products_file, "w", encoding="utf-8") as f:
                json.dump(products, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка сохранения товаров: {e}")
            
    return res


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


# ── MC SMS Auth Endpoints ────────────────────────────────────

@app.post("/api/start-login")
def start_login(req: PhoneRequest):
    phone = req.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Не передан номер телефона")
    
    success, msg = worker.send_cmd(Command.START_LOGIN, phone)
    if success:
        return {"success": True, "message": msg}
    else:
        raise HTTPException(status_code=400, detail=msg)


@app.post("/api/submit-sms")
def submit_sms(req: SmsRequest):
    sms = req.sms.strip()
    if not sms:
        raise HTTPException(status_code=400, detail="Не передан SMS-код")
        
    success, msg = worker.send_cmd(Command.SUBMIT_SMS, sms)
    if success:
        return {"success": True, "message": msg}
    else:
        raise HTTPException(status_code=400, detail=msg)


@app.get("/api/mc-status")
def get_mc_status(verify: bool = False):
    has_session = os.path.exists("state.json")
    if not has_session:
        return {"success": True, "has_session": False, "valid": False}
        
    if verify:
        success, valid = worker.send_cmd(Command.VERIFY_SESSION)
        if success:
            return {"success": True, "has_session": True, "valid": valid}
        else:
            return {"success": False, "detail": valid}
            
    return {"success": True, "has_session": True, "valid": True}

@app.post("/api/close")
def close_session():
    return {"success": True}
