import os
import json
import math
import queue
import threading
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.sync_api import sync_playwright

app = FastAPI(title="Kaspi SaaS Wrapper API")

# Enable CORS for frontend Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_state_file_path(phone: str) -> str:
    sanitized = "".join(c for c in phone if c.isdigit())
    os.makedirs("states", exist_ok=True)
    return f"states/state_{sanitized}.json"

class Command:
    START_LOGIN = "start_login"
    SUBMIT_SMS = "submit_sms"
    VERIFY_SESSION = "verify_session"
    FETCH_PRODUCTS = "fetch_products"
    PRODUCT_ACTION = "product_action"
    FETCH_ORDERS = "fetch_orders"
    PACK_ORDER = "pack_order"
    FETCH_TRANSFERS = "fetch_transfers"
    CLEANUP = "cleanup"

class PlaywrightWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.cmd_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.current_phone = None

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
                        self.current_phone = phone
                        
                        if browser:
                            try:
                                browser.close()
                            except:
                                pass
                            browser, context, page = None, None, None
                        
                        # Launch headful browser so the user can verify visually/submit sms
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
                        
                        page.wait_for_timeout(4000)
                        
                        if "login" in page.url:
                            raise Exception("Вход не удался. Пожалуйста, проверьте код из SMS.")
                        
                        # Save session state for the specific phone number
                        if self.current_phone:
                            state_path = get_state_file_path(self.current_phone)
                            context.storage_state(path=state_path)
                        else:
                            raise Exception("Не найден привязанный номер телефона для сессии.")
                        
                        browser.close()
                        browser, context, page = None, None, None
                        self.result_queue.put((True, "Success"))
                        
                    elif cmd == Command.VERIFY_SESSION:
                        phone = data
                        state_path = get_state_file_path(phone)
                        if os.path.exists(state_path):
                            temp_browser = playwright.chromium.launch(headless=True)
                            temp_context = temp_browser.new_context(storage_state=state_path)
                            temp_page = temp_context.new_page()
                            temp_page.goto("https://kaspi.kz/mc/#/orders?status=NEW")
                            temp_page.wait_for_timeout(3000)
                            
                            is_valid = "login" not in temp_page.url
                            temp_browser.close()
                            self.result_queue.put((True, is_valid))
                        else:
                            self.result_queue.put((True, False))

                    elif cmd == Command.FETCH_PRODUCTS:
                        phone, page_num, status, search_query, page_size = data
                        state_path = get_state_file_path(phone)
                        if not os.path.exists(state_path):
                            raise Exception("Сессия авторизации не найдена.")
                        
                        temp_browser = playwright.chromium.launch(headless=True)
                        try:
                            temp_context = temp_browser.new_context(storage_state=state_path)
                            temp_page = temp_context.new_page()
                            
                            from backend.kaspi_wrapper import KaspiWrapper
                            result_data = KaspiWrapper.scrape_products(temp_page, status, page_num, search_query, page_size)
                            
                            temp_browser.close()
                            self.result_queue.put((True, result_data))
                        except Exception as inner_e:
                            try:
                                temp_browser.close()
                            except:
                                pass
                            raise inner_e

                    elif cmd == Command.PRODUCT_ACTION:
                        phone, sku, action_type, payload = data
                        state_path = get_state_file_path(phone)
                        if not os.path.exists(state_path):
                            raise Exception("Сессия авторизации не найдена.")
                        
                        temp_browser = playwright.chromium.launch(headless=True)
                        try:
                            temp_context = temp_browser.new_context(storage_state=state_path)
                            temp_page = temp_context.new_page()
                            
                            from backend.kaspi_wrapper import KaspiWrapper
                            msg = KaspiWrapper.perform_product_action(temp_page, sku, action_type, payload)
                            
                            temp_context.storage_state(path=state_path)
                            temp_browser.close()
                            self.result_queue.put((True, msg))
                        except Exception as inner_e:
                            try:
                                temp_browser.close()
                            except:
                                pass
                            raise inner_e

                    elif cmd == Command.FETCH_ORDERS:
                        phone, status = data
                        state_path = get_state_file_path(phone)
                        if not os.path.exists(state_path):
                            raise Exception("Сессия авторизации не найдена.")
                        
                        temp_browser = playwright.chromium.launch(headless=True)
                        try:
                            temp_context = temp_browser.new_context(storage_state=state_path)
                            temp_page = temp_context.new_page()
                            
                            from backend.kaspi_wrapper import KaspiWrapper
                            orders = KaspiWrapper.fetch_orders(temp_page, status)
                            
                            temp_browser.close()
                            self.result_queue.put((True, orders))
                        except Exception as inner_e:
                            try:
                                temp_browser.close()
                            except:
                                pass
                            raise inner_e

                    elif cmd == Command.PACK_ORDER:
                        phone, order_id = data
                        state_path = get_state_file_path(phone)
                        if not os.path.exists(state_path):
                            raise Exception("Сессия авторизации не найдена.")
                        
                        temp_browser = playwright.chromium.launch(headless=True)
                        try:
                            temp_context = temp_browser.new_context(storage_state=state_path)
                            temp_page = temp_context.new_page()
                            
                            from backend.kaspi_wrapper import KaspiWrapper
                            success = KaspiWrapper.pack_order(temp_page, order_id)
                            
                            temp_context.storage_state(path=state_path)
                            temp_browser.close()
                            self.result_queue.put((True, success))
                        except Exception as inner_e:
                            try:
                                temp_browser.close()
                            except:
                                pass
                            raise inner_e

                    elif cmd == Command.FETCH_TRANSFERS:
                        phone, delivery_filter = data
                        state_path = get_state_file_path(phone)
                        if not os.path.exists(state_path):
                            raise Exception("Сессия авторизации не найдена.")
                        
                        temp_browser = playwright.chromium.launch(headless=True)
                        try:
                            temp_context = temp_browser.new_context(storage_state=state_path)
                            temp_page = temp_context.new_page()
                            
                            from backend.kaspi_wrapper import KaspiWrapper
                            transfers = KaspiWrapper.fetch_transfers(temp_page, delivery_filter)
                            
                            temp_browser.close()
                            self.result_queue.put((True, transfers))
                        except Exception as inner_e:
                            try:
                                temp_browser.close()
                            except:
                                pass
                            raise inner_e
                            
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

# Global worker instance
worker = PlaywrightWorker()

@app.on_event("startup")
def startup_event():
    worker.start()

@app.on_event("shutdown")
def shutdown_event():
    worker.send_cmd(Command.CLEANUP)

class PhoneRequest(BaseModel):
    phone: str

class SmsRequest(BaseModel):
    phone: str
    sms: str

class ProductActionRequest(BaseModel):
    phone: str
    sku: str
    action: str
    price: int = 0
    stock: int = 100

@app.get("/")
def read_root():
    return {"status": "FastAPI Kaspi SaaS Wrapper is running"}

@app.get("/api/status")
def get_status(phone: str = Query(...)):
    state_path = get_state_file_path(phone)
    has_session = os.path.exists(state_path)
    return {"success": True, "has_session": has_session}

@app.post("/api/start-login")
def start_login(req: PhoneRequest):
    success, msg = worker.send_cmd(Command.START_LOGIN, req.phone)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}

@app.post("/api/submit-sms")
def submit_sms(req: SmsRequest):
    success, msg = worker.send_cmd(Command.SUBMIT_SMS, req.sms)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}

@app.post("/api/verify-session")
def verify_session(req: PhoneRequest):
    success, valid = worker.send_cmd(Command.VERIFY_SESSION, req.phone)
    if not success:
        raise HTTPException(status_code=400, detail=str(valid))
    return {"success": True, "valid": valid}

@app.get("/api/products")
def get_products(
    phone: str = Query(...),
    page: int = Query(1),
    status: str = Query("active"),
    search: str = Query(""),
    page_size: int = Query(10)
):
    success, data = worker.send_cmd(Command.FETCH_PRODUCTS, (phone, page, status, search, page_size))
    if not success:
        raise HTTPException(status_code=400, detail=str(data))
    return {
        "success": True,
        "products": data["products"],
        "total_count": data["total_count"],
        "total_pages": data["total_pages"]
    }

@app.post("/api/product-action")
def run_product_action(req: ProductActionRequest):
    payload = (req.price, req.stock)
    success, msg = worker.send_cmd(Command.PRODUCT_ACTION, (req.phone, req.sku, req.action, payload))
    if not success:
        raise HTTPException(status_code=400, detail=str(msg))
    return {"success": True, "message": msg}

@app.get("/api/orders")
def get_orders(phone: str = Query(...), status: str = Query("KASPI_DELIVERY_CARGO_ASSEMBLY")):
    success, data = worker.send_cmd(Command.FETCH_ORDERS, (phone, status))
    if not success:
        raise HTTPException(status_code=400, detail=str(data))
    return {"success": True, "orders": data}

class PackOrderRequest(BaseModel):
    phone: str
    order_id: str

@app.post("/api/orders/pack")
def pack_order(req: PackOrderRequest):
    success, data = worker.send_cmd(Command.PACK_ORDER, (req.phone, req.order_id))
    if not success:
        raise HTTPException(status_code=400, detail=str(data))
    return {"success": True, "message": "Заказ успешно собран (накладные сформированы)"}

@app.get("/api/transfers")
def get_transfers(phone: str = Query(...), filter: str = Query("ALL")):
    success, data = worker.send_cmd(Command.FETCH_TRANSFERS, (phone, filter))
    if not success:
        raise HTTPException(status_code=400, detail=str(data))
    return {"success": True, "transfers": data}

@app.post("/api/close")
def close_session():
    worker.send_cmd(Command.CLEANUP)
    return {"success": True}
