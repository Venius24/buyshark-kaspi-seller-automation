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
    # Маскируем API ключи для безопасности на клиенте (показываем перфикс)
    masked_deepseek = (cfg.get("deepseek_api_key", "")[:6] + "..." + cfg.get("deepseek_api_key", "")[-4:]) if len(cfg.get("deepseek_api_key", "")) > 10 else cfg.get("deepseek_api_key", "")
    masked_tg = (cfg.get("tg_bot_token", "")[:6] + "..." + cfg.get("tg_bot_token", "")[-4:]) if len(cfg.get("tg_bot_token", "")) > 10 else cfg.get("tg_bot_token", "")

    return {
        "success": True,
        "config": {
            "enabled": cfg.get("enabled", True),
            "deepseek_api_key": cfg.get("deepseek_api_key", ""),
            "deepseek_api_key_masked": masked_deepseek,
            "tg_bot_token": cfg.get("tg_bot_token", ""),
            "tg_bot_token_masked": masked_tg,
            "admin_ids": cfg.get("admin_ids", []),
            "use_tg_proxy": cfg.get("use_tg_proxy", False),
            "tg_proxy": cfg.get("tg_proxy", ""),
            "merchant_id": cfg.get("merchant_id", "")
        }
    }

@router.post("/config")
def save_chatbot_config(req: ChatbotConfigRequest):
    """Сохранить или обновить конфигурацию чат-бота."""
    update_data = {}
    if req.enabled is not None:
        update_data["enabled"] = req.enabled
    if req.deepseek_api_key is not None:
        update_data["deepseek_api_key"] = req.deepseek_api_key.strip()
    if req.tg_bot_token is not None:
        update_data["tg_bot_token"] = req.tg_bot_token.strip()
    if req.admin_ids is not None:
        update_data["admin_ids"] = req.admin_ids
    if req.use_tg_proxy is not None:
        update_data["use_tg_proxy"] = req.use_tg_proxy
    if req.tg_proxy is not None:
        update_data["tg_proxy"] = req.tg_proxy.strip()

    updated = config.save_chatbot_config(update_data)
    return {
        "success": True,
        "message": "Конфигурация ИИ-автоответчика успешно сохранена.",
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
