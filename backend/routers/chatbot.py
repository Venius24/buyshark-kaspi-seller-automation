import logging
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from backend.chatbot import config
from backend.chatbot.kaspi import load_chat_cookies
from backend.security.store_context import StoreContext, get_store_context
from backend.state_helpers import _save_chat_cookies_for_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])

class ChatbotConfigRequest(BaseModel):
    enabled: Optional[bool] = True
    admin_ids: Optional[List[str]] = None

class UnblockChatRequest(BaseModel):
    group_id: str

class MobileLoginStartRequest(BaseModel):
    phone: str

class MobileLoginSubmitRequest(BaseModel):
    code: str

@router.get("/config")
def get_chatbot_config(ctx: StoreContext = Depends(get_store_context)):
    """Получить конфигурацию ИИ-автоответчика этого магазина."""
    cfg = config.load_chatbot_config(ctx.store_id)
    admin_ids = config.load_admin_ids(ctx.store_id)

    return {
        "success": True,
        "config": {
            "enabled": cfg.get("enabled", True),
            "admin_ids": admin_ids,
            "merchant_id": cfg.get("merchant_id", "")
        }
    }

@router.post("/config")
def save_chatbot_config(req: ChatbotConfigRequest, ctx: StoreContext = Depends(get_store_context)):
    """Сохранить пользовательские настройки чат-бота (admin_ids, enabled) этого магазина."""
    update_data = {}
    if req.enabled is not None:
        update_data["enabled"] = req.enabled
    if req.admin_ids is not None:
        update_data["admin_ids"] = req.admin_ids

    updated = config.save_chatbot_config(update_data, ctx.store_id)
    return {
        "success": True,
        "message": "Настройки уведомлений чат-бота сохранены.",
        "config": updated
    }

@router.get("/status")
def get_chatbot_status(ctx: StoreContext = Depends(get_store_context)):
    """Получить статус сессии, блокировки и активных диалогов чат-бота этого магазина."""
    cfg = config.load_chatbot_config(ctx.store_id)
    # Статус чата отражает отдельную SMS-сессию (encrypted_chat_cookies), а
    # не общие MC-куки email+пароль входа — чат-API Kaspi требует именно её.
    chat_cookies = load_chat_cookies(store_id=ctx.store_id)
    has_valid_cookies = bool("mc-session" in chat_cookies and "mc-sid" in chat_cookies)

    blocked_chats = [
        {"group_id": gid, "reason": "HUMAN_OVERRIDE"}
        for gid in config.list_blocked_chat_ids(ctx.store_id)
    ]

    return {
        "success": True,
        "enabled": cfg.get("enabled", True),
        "has_session": has_valid_cookies,
        "cookie_alert_sent": config.is_cookie_alert_sent(ctx.store_id),
        "blocked_chats_count": len(blocked_chats),
        "blocked_chats": blocked_chats,
        "processed_chats_count": config.count_processed_chats(ctx.store_id)
    }

@router.post("/unblock")
def unblock_chat(req: UnblockChatRequest, ctx: StoreContext = Depends(get_store_context)):
    """Разблокировать чат (HUMAN_OVERRIDE) этого магазина из дашборда UI."""
    gid = req.group_id.strip()
    if config.clear_chat_human_override(ctx.store_id, gid):
        return {"success": True, "message": f"Чат {gid} успешно разблокирован для ИИ."}

    return {"success": True, "message": f"Чат {gid} не был заблокирован."}

@router.post("/mobile-login/start")
def start_mobile_login(req: MobileLoginStartRequest, ctx: StoreContext = Depends(get_store_context)):
    """
    Шаг 1 из 2 повторного входа в Kaspi-чат: отправляет телефон, Kaspi
    высылает SMS-код. Держит свой Chromium открытым до SUBMIT (де-
    глобализация Playwright — сессия SMS-входа привязана к store_id, не к
    общему на все магазины браузеру, см. playwright_worker.py).
    """
    from backend.services.playwright_worker import worker, Command
    success, msg, _cookies = worker.send_cmd(Command.START_SMS_LOGIN, req.phone, store_id=ctx.store_id)
    if success:
        return {"success": True, "message": "Код из SMS отправлен на указанный номер."}
    raise HTTPException(status_code=400, detail=msg)

@router.post("/mobile-login/submit-code")
def submit_mobile_login_code(req: MobileLoginSubmitRequest, ctx: StoreContext = Depends(get_store_context)):
    """Шаг 2 из 2: код из SMS завершает вход, куки сохраняются в Store.encrypted_chat_cookies этого стора."""
    from backend.services.playwright_worker import worker, Command
    success, msg, fresh_cookies = worker.send_cmd(Command.SUBMIT_SMS_LOGIN, req.code, store_id=ctx.store_id)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    if not _save_chat_cookies_for_store(ctx.store_id, fresh_cookies):
        raise HTTPException(status_code=400, detail="Вход выполнен, но не удалось сохранить куки чата.")

    config.set_cookie_alert_sent(ctx.store_id, False)

    return {"success": True, "message": "Вход в чат Kaspi выполнен, сессия сохранена."}
