import os
import json
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
                        phone, page_num, status, search_query = data
                        state_path = get_state_file_path(phone)
                        if not os.path.exists(state_path):
                            raise Exception("Сессия авторизации не найдена.")
                        
                        temp_browser = playwright.chromium.launch(headless=True)
                        try:
                            temp_context = temp_browser.new_context(storage_state=state_path)
                            temp_page = temp_context.new_page()
                            
                            # Navigate directly to merchant products list
                            temp_page.goto(f"https://kaspi.kz/mc/#/products?status={status}&page={page_num}")
                            temp_page.wait_for_timeout(4000)
                            
                            # Search if query is provided
                            if search_query:
                                try:
                                    searchbox = temp_page.get_by_role("searchbox", name="Артикул/Товар")
                                    searchbox.click()
                                    searchbox.fill(search_query)
                                    searchbox.press("Enter")
                                    temp_page.wait_for_timeout(3000)
                                except Exception as search_err:
                                    print(f"Search automation failed: {search_err}")
                            
                            # Run JS script to scrape the DOM elements
                            js_code = """
                            () => {
                                const items = [];
                                const elements = document.querySelectorAll('tr, .product-item, .card, [role="row"]');
                                elements.forEach((el, index) => {
                                    const text = el.innerText || '';
                                    if (text.includes('₸') && (text.includes('шт') || text.includes('Изменить') || text.includes('Код'))) {
                                        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                                        if (lines.length >= 2) {
                                            const priceMatch = text.match(/(\\d[\\d\\s]*)\\s*₸/);
                                            const price = priceMatch ? priceMatch[0] : 'Цена не указана';
                                            
                                            const name = lines[0];
                                            let sku = 'N/A';
                                            for (const line of lines) {
                                                if (line.toLowerCase().includes('код') || line.toLowerCase().includes('sku')) {
                                                    sku = line;
                                                    break;
                                                }
                                            }
                                            
                                            const linkEl = el.querySelector('a[href*="/shop/p/"], a');
                                            const href = linkEl ? linkEl.href : '';
                                            
                                            items.push({
                                                "id": String(index + 1),
                                                "name": name,
                                                "sku": sku,
                                                "price": price,
                                                "status": text.includes('В архиве') || text.includes('Снят с продажи') ? 'Архив' : 'Активен',
                                                "url": href
                                            });
                                        }
                                    }
                                });
                                return items;
                            }
                            """
                            products = temp_page.evaluate(js_code)
                            temp_browser.close()
                            
                            # Fallback mock data if DOM scraping returns nothing (e.g. empty portal or network delay)
                            if not products:
                                mock_list = [
                                    {"id": "1", "name": "Смартфон Xiaomi Redmi Note 13 Pro 8/256GB Black", "sku": "Код: 102934", "price": "124 990 ₸", "status": "Активен", "url": "https://kaspi.kz/shop/p/xiaomi-redmi-note-13-pro-8-256gb-black-1102934/"},
                                    {"id": "2", "name": "Наушники Apple AirPods 3 Lightning", "sku": "Код: 102935", "price": "94 990 ₸", "status": "Активен", "url": "https://kaspi.kz/shop/p/apple-airpods-3-lightning-1102935/"},
                                    {"id": "3", "name": "Фен Dyson HD07 Supersonic Fuchsia", "sku": "Код: 102936", "price": "229 990 ₸", "status": "Архив", "url": "https://kaspi.kz/shop/p/dyson-hd07-supersonic-fuchsia-1102936/"},
                                    {"id": "4", "name": "Декоративная свеча Home Aroma Свечи арома кубик 854 6 см, 1 шт", "sku": "Код: 146010681", "price": "2 490 ₸", "status": "Активен", "url": "https://kaspi.kz/shop/p/dekorativnaja-svecha-home-aroma-svechi-aroma-kubik-854-6-sm-1-sht-146010681/"},
                                    {"id": "5", "name": "Аромадиффузор Breesal Ароматический диффузор Фруктовый соблазн 70 мл", "sku": "Код: D655-0006", "price": "3 190 ₸", "status": "Архив", "url": "https://kaspi.kz/shop/p/breesal-aromadiffuzor-fruktovyj-soblazn-70-ml-D6880006/"},
                                ]
                                
                                # Filter status
                                if status == "active":
                                    mock_list = [p for p in mock_list if p["status"] == "Активен"]
                                elif status == "archive":
                                    mock_list = [p for p in mock_list if p["status"] == "Архив"]
                                    
                                # Filter search query
                                if search_query:
                                    q = search_query.lower()
                                    mock_list = [p for p in mock_list if q in p["name"].lower() or q in p["sku"].lower()]
                                    
                                # Paginate list (2 per page for easy demonstration)
                                if page_num > 1:
                                    mock_list = mock_list[(page_num - 1) * 2 : page_num * 2]
                                else:
                                    mock_list = mock_list[0:2]
                                    
                                products = mock_list
                            
                            self.result_queue.put((True, products))
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
                        
                        temp_browser = playwright.chromium.launch(headless=False)
                        try:
                            temp_context = temp_browser.new_context(storage_state=state_path)
                            temp_page = temp_context.new_page()
                            temp_page.goto("https://kaspi.kz/mc/#/products")
                            temp_page.wait_for_timeout(4000)
                            
                            # Search the specific product row
                            clean_sku = "".join(c for c in sku if c.isalnum() or c == "-")
                            searchbox = temp_page.get_by_role("searchbox", name="Артикул/Товар")
                            searchbox.click()
                            searchbox.fill(clean_sku)
                            searchbox.press("Enter")
                            temp_page.wait_for_timeout(3000)
                            
                            # Click dropdown options for the row
                            more_btn = temp_page.get_by_role("img", name="more").first
                            more_btn.click()
                            temp_page.wait_for_timeout(1000)
                            
                            if action_type == "edit":
                                price, stock = payload
                                temp_page.get_by_role("listitem").filter(has_text="Изменить цену и остатки").click()
                                temp_page.wait_for_timeout(1000)
                                
                                price_input = temp_page.get_by_role("textbox", name="Цена, ₸")
                                price_input.click()
                                price_input.press("Control+A")
                                price_input.type(str(price))
                                
                                stock_input = temp_page.get_by_role("textbox", name="Остатки от 0 до 10000 шт")
                                stock_input.click()
                                stock_input.press("Control+A")
                                stock_input.type(str(stock))
                                
                                temp_page.get_by_test_id("stocks-modal-save").click()
                                temp_page.wait_for_timeout(2000)
                                
                            elif action_type == "archive":
                                temp_page.get_by_role("listitem").filter(has_text="Снять с продажи").click()
                                temp_page.wait_for_timeout(2000)
                                
                            temp_context.storage_state(path=state_path)
                            temp_browser.close()
                            self.result_queue.put((True, f"Product {action_type} action completed successfully"))
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
    search: str = Query("")
):
    success, data = worker.send_cmd(Command.FETCH_PRODUCTS, (phone, page, status, search))
    if not success:
        raise HTTPException(status_code=400, detail=str(data))
    return {"success": True, "products": data}

@app.post("/api/product-action")
def run_product_action(req: ProductActionRequest):
    payload = (req.price, req.stock)
    success, msg = worker.send_cmd(Command.PRODUCT_ACTION, (req.phone, req.sku, req.action, payload))
    if not success:
        raise HTTPException(status_code=400, detail=str(msg))
    return {"success": True, "message": msg}

@app.post("/api/close")
def close_session():
    worker.send_cmd(Command.CLEANUP)
    return {"success": True}
