import logging
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from backend.chatbot import config
from backend.chatbot.kaspi import load_cookies
from backend.security.store_context import StoreContext, get_store_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])

class ChatbotConfigRequest(BaseModel):
    enabled: Optional[bool] = True
    admin_ids: Optional[List[str]] = None

class UnblockChatRequest(BaseModel):
    group_id: str

@router.get("/config")
def get_chatbot_config(ctx: StoreContext = Depends(get_store_context)):
    """Получить конфигурацию ИИ-автоответчика."""
    cfg = config.load_chatbot_config()
    admin_ids = config.load_admin_ids()

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
    """Сохранить пользовательские настройки чат-бота (admin_ids, enabled)."""
    update_data = {}
    if req.enabled is not None:
        update_data["enabled"] = req.enabled
    if req.admin_ids is not None:
        update_data["admin_ids"] = req.admin_ids

    updated = config.save_chatbot_config(update_data)
    return {
        "success": True,
        "message": "Настройки уведомлений чат-бота сохранены.",
        "config": updated
    }

@router.get("/status")
def get_chatbot_status(ctx: StoreContext = Depends(get_store_context)):
    """Получить статус сессии, блокировки и активных диалогов чат-бота."""
    cfg = config.load_chatbot_config()
    cookies = load_cookies()
    has_valid_cookies = bool("mc-session" in cookies and "mc-sid" in cookies)

    blocked_chats = [
        {"group_id": gid, "reason": "HUMAN_OVERRIDE"}
        for gid, val in config.PROCESSED_CHATS.items()
        if val == "HUMAN_OVERRIDE"
    ]

    return {
        "success": True,
        "enabled": cfg.get("enabled", True),
        "has_session": has_valid_cookies,
        "cookie_alert_sent": config.COOKIE_ALERT_SENT,
        "blocked_chats_count": len(blocked_chats),
        "blocked_chats": blocked_chats,
        "processed_chats_count": len(config.PROCESSED_CHATS)
    }

@router.post("/unblock")
def unblock_chat(req: UnblockChatRequest, ctx: StoreContext = Depends(get_store_context)):
    """Разблокировать чат (HUMAN_OVERRIDE) из дашборда UI."""
    gid = req.group_id.strip()
    if config.PROCESSED_CHATS.get(gid) == "HUMAN_OVERRIDE":
        config.PROCESSED_CHATS.pop(gid, None)
        return {"success": True, "message": f"Чат {gid} успешно разблокирован для ИИ."}
    
    return {"success": True, "message": f"Чат {gid} не был заблокирован."}
