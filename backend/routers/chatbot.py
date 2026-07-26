import logging
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from backend.chatbot import config
from backend.chatbot.kaspi import load_cookies

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])

class ChatbotConfigRequest(BaseModel):
    enabled: Optional[bool] = True
    deepseek_api_key: Optional[str] = None
    tg_bot_token: Optional[str] = None
    admin_ids: Optional[List[str]] = None
    use_tg_proxy: Optional[bool] = False
    tg_proxy: Optional[str] = None

class UnblockChatRequest(BaseModel):
    group_id: str

@router.get("/config")
def get_chatbot_config():
    """Получить конфигурацию ИИ-автоответчика."""
    cfg = config.load_chatbot_config()
    raw_deepseek = cfg.get("deepseek_api") or cfg.get("deepseek_api_key") or ""
    raw_tg_token = cfg.get("tg_bot_token", "")
    raw_tg_proxy = cfg.get("tg_bot_proxy") or cfg.get("tg_proxy") or ""

    masked_deepseek = (raw_deepseek[:6] + "..." + raw_deepseek[-4:]) if len(raw_deepseek) > 10 else ("Configured in .env" if raw_deepseek else "")
    masked_tg = (raw_tg_token[:6] + "..." + raw_tg_token[-4:]) if len(raw_tg_token) > 10 else ("Configured in .env" if raw_tg_token else "")

    return {
        "success": True,
        "config": {
            "enabled": cfg.get("enabled", True),
            "deepseek_api": raw_deepseek,
            "deepseek_api_key": raw_deepseek,
            "deepseek_api_key_masked": masked_deepseek,
            "tg_bot_token": raw_tg_token,
            "tg_bot_token_masked": masked_tg,
            "tg_bot_proxy": raw_tg_proxy,
            "tg_proxy": raw_tg_proxy,
            "use_tg_proxy": bool(raw_tg_proxy),
            "admin_ids": cfg.get("admin_ids", []),
            "merchant_id": cfg.get("merchant_id", "")
        }
    }

@router.post("/config")
def save_chatbot_config(req: ChatbotConfigRequest):
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
def get_chatbot_status():
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
def unblock_chat(req: UnblockChatRequest):
    """Разблокировать чат (HUMAN_OVERRIDE) из дашборда UI."""
    gid = req.group_id.strip()
    if config.PROCESSED_CHATS.get(gid) == "HUMAN_OVERRIDE":
        config.PROCESSED_CHATS.pop(gid, None)
        return {"success": True, "message": f"Чат {gid} успешно разблокирован для ИИ."}
    
    return {"success": True, "message": f"Чат {gid} не был заблокирован."}
