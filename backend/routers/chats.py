import logging
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from backend.chatbot.kaspi import load_chat_cookies
from backend.chatbot import chat_client, config as chatbot_config
from backend.dumper.config import discover_merchant_id
from backend.security.store_context import StoreContext, get_store_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chats", tags=["chats"])


class SendMessageRequest(BaseModel):
    text: str
    # Быстрые ответы (см. фронт) отправляют текст и сразу забирают чат у
    # ИИ одним запросом, чтобы бот не ответил повторно поверх ручного ответа.
    override: Optional[bool] = False


class ReadReceiptItem(BaseModel):
    id: str
    created: int


class MarkReadRequest(BaseModel):
    messages: List[ReadReceiptItem]


def _require_chat_session(ctx: StoreContext) -> dict:
    cookies = load_chat_cookies(store_id=ctx.store_id)
    if not cookies:
        raise HTTPException(
            status_code=400,
            detail="Сессия чата Kaspi не активна. Войдите через SMS во вкладке «ИИ-Автоответчик».",
        )
    return cookies


def _resolve_merchant_id(ctx: StoreContext) -> str:
    return discover_merchant_id(ctx.token) or ctx.merchant_id


@router.get("")
async def list_chats(ctx: StoreContext = Depends(get_store_context)):
    cookies = _require_chat_session(ctx)
    merchant_id = _resolve_merchant_id(ctx)
    groups = await chat_client.list_groups(cookies, merchant_id)
    for g in groups:
        g["is_human_override"] = chatbot_config.is_chat_human_override(ctx.store_id, g.get("id"))
    return {"success": True, "chats": groups}


@router.get("/{group_id}/messages")
async def get_chat_messages(group_id: str, ctx: StoreContext = Depends(get_store_context)):
    cookies = _require_chat_session(ctx)
    merchant_id = _resolve_merchant_id(ctx)
    messages = await chat_client.get_history(group_id, cookies, merchant_id)
    return {"success": True, "messages": messages}


@router.post("/{group_id}/messages")
async def send_chat_message(group_id: str, req: SendMessageRequest, ctx: StoreContext = Depends(get_store_context)):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Текст сообщения не может быть пустым.")
    cookies = _require_chat_session(ctx)
    merchant_id = _resolve_merchant_id(ctx)
    result = await chat_client.send_message(group_id, text, cookies, merchant_id)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=f"Kaspi отклонил отправку: {result.get('error')}")
    if req.override:
        chatbot_config.set_chat_human_override(ctx.store_id, group_id)
    return {"success": True, "message": result.get("message")}


@router.post("/{group_id}/take-over")
async def take_over_chat(group_id: str, ctx: StoreContext = Depends(get_store_context)):
    """
    Забирает диалог у ИИ без отправки сообщения — менеджер продолжает
    переписку 1-на-1 сам. Та же per-store блокировка (chat_states.human_override),
    что использует эскалация бота (backend/chatbot/notifications.py);
    снять можно уже существующим POST /api/chatbot/unblock.
    """
    _require_chat_session(ctx)  # чат должен быть реально доступен этому стору
    chatbot_config.set_chat_human_override(ctx.store_id, group_id)
    return {"success": True}


@router.post("/{group_id}/read")
async def mark_chat_read(group_id: str, req: MarkReadRequest, ctx: StoreContext = Depends(get_store_context)):
    cookies = _require_chat_session(ctx)
    merchant_id = _resolve_merchant_id(ctx)
    ok = await chat_client.mark_read(
        group_id,
        [{"id": m.id, "created": m.created} for m in req.messages],
        cookies,
        merchant_id,
    )
    return {"success": ok}
