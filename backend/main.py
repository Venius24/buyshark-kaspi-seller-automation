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

@app.post("/api/close")
def close_session():
    worker.send_cmd(Command.CLEANUP)
    return {"success": True}
