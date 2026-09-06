"""HTTP-слой ИИ-помощника кабинета (каркас).

Всё store-scoped через require_section("assistant", ...) — тот же гейт, что и
у остальных разделов: сотрудник видит помощника, только если раздел ему открыт.
Ключей и реальной модели тут нет: без ASSISTANT_API_KEY провайдер — заглушка,
эндпоинты при этом работают полностью (см. backend/assistant/provider.py).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.assistant import storage
from backend.assistant import tools as tool_registry
from backend.assistant.config import load_config
from backend.assistant.engine import run_turn
from backend.assistant.prompt import StoreBrief
from backend.assistant.provider import ROLE_ASSISTANT, ROLE_USER, get_provider
from backend.assistant.tools import ToolContext
from backend.db.database import get_db
from backend.db.models import Store
from backend.security.store_context import StoreContext
from backend.security.store_roles import require_section

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

# Читать помощника может любой, кому открыт раздел; писать — тоже VIEWER:
# сам по себе диалог ничего в магазине не меняет, а изменяющие инструменты
# закрыты отдельно (tools.py: read_only + подтверждение + min_role).
_read_dep = require_section("assistant")
_write_dep = require_section("assistant")


class NewConversationRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=255)


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    # Пользователь подтвердил изменяющее действие в UI. Пока ни один
    # инструмент не пишет, но флаг закладывается сразу — иначе потом придётся
    # менять контракт эндпоинта.
    confirmed: bool = False


def _convo_out(row) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _message_out(row) -> dict:
    return {
        "id": row.id,
        "role": row.role,
        "content": row.content,
        "tool_trace": storage.load_trace(row.tool_trace),
        "provider": row.provider,
        "model": row.model,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _brief(db: AsyncSession, ctx: StoreContext) -> StoreBrief:
    store = (await db.execute(select(Store).where(Store.id == ctx.store_id))).scalar_one_or_none()
    return StoreBrief(
        store_id=ctx.store_id,
        store_name=store.name if store else None,
        merchant_id=ctx.merchant_id,
        user_role=ctx.role,
    )


@router.get("/status")
def get_status(ctx: StoreContext = Depends(_read_dep)):
    """Что сейчас включено. Ключ наружу не отдаём — только факт его наличия."""
    cfg = load_config()
    provider = get_provider(cfg)
    tool_ctx = ToolContext(store_id=ctx.store_id, role=ctx.role, token=ctx.token, sections=ctx.sections)
    specs = tool_registry.available_specs(tool_ctx, allow_write=cfg.allow_write_tools)
    return {
        "success": True,
        "enabled": cfg.enabled,
        "provider": provider.name,
        "model": cfg.model,
        "has_api_key": cfg.has_api_key,
        "stub_mode": cfg.is_stub,
        "allow_write_tools": cfg.allow_write_tools,
        "tools": [
            {"name": s.name, "description": s.description, "read_only": s.read_only, "min_role": s.min_role}
            for s in specs
        ],
    }


@router.get("/conversations")
async def get_conversations(ctx: StoreContext = Depends(_read_dep), db: AsyncSession = Depends(get_db)):
    rows = await storage.list_conversations(db, ctx.store_id)
    return {"success": True, "conversations": [_convo_out(r) for r in rows]}


@router.post("/conversations")
async def create_conversation(
    req: NewConversationRequest,
    ctx: StoreContext = Depends(_write_dep),
    db: AsyncSession = Depends(get_db),
):
    convo = await storage.create_conversation(db, ctx.store_id, _user_id(ctx), req.title or "")
    return {"success": True, "conversation": _convo_out(convo)}


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    ctx: StoreContext = Depends(_read_dep),
    db: AsyncSession = Depends(get_db),
):
    convo = await storage.get_conversation(db, ctx.store_id, conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    rows = await storage.list_messages(db, ctx.store_id, conversation_id)
    return {"success": True, "conversation": _convo_out(convo), "messages": [_message_out(r) for r in rows]}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    req: SendMessageRequest,
    ctx: StoreContext = Depends(_write_dep),
    db: AsyncSession = Depends(get_db),
):
    cfg = load_config()
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail="ИИ-помощник выключен (ASSISTANT_ENABLED=0)")

    convo = await storage.get_conversation(db, ctx.store_id, conversation_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Диалог не найден")

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    prior = await storage.list_messages(db, ctx.store_id, conversation_id)
    history = storage.to_llm_history(prior, cfg.history_limit)

    user_row = await storage.append_message(db, ctx.store_id, conversation_id, ROLE_USER, text)

    # Первый вопрос заодно становится названием диалога — иначе список
    # переписок это стена одинаковых «Новый диалог».
    if not prior and convo.title in ("", "Новый диалог"):
        convo.title = text[:80]

    turn = await run_turn(
        user_text=text,
        history=history,
        brief=await _brief(db, ctx),
        ctx=ToolContext(
            store_id=ctx.store_id,
            user_id=_user_id(ctx),
            role=ctx.role,
            confirmed=req.confirmed,
            # Токен Kaspi и разделы кабинета — инструменты читают заказы и
            # каталог из Kaspi по токену магазина и обязаны уважать вторую
            # ось прав (см. backend/security/sections.py).
            token=ctx.token,
            sections=ctx.sections,
        ),
        cfg=cfg,
    )

    if turn.error:
        # Ход пользователя уже сохранён — ответ не сохраняем, чтобы повтор
        # не тащил в историю пустое сообщение помощника.
        raise HTTPException(status_code=502, detail=turn.error)

    reply_row = await storage.append_message(
        db,
        ctx.store_id,
        conversation_id,
        ROLE_ASSISTANT,
        turn.reply,
        tool_trace=turn.tool_trace,
        provider=turn.provider,
        usage=turn.usage,
    )

    return {
        "success": True,
        "conversation": _convo_out(convo),
        "messages": [_message_out(user_row), _message_out(reply_row)],
        "truncated": turn.truncated,
        "stub_mode": cfg.is_stub,
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    ctx: StoreContext = Depends(_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Мягкое удаление: переписка помечается archived, сообщения остаются."""
    ok = await storage.archive_conversation(db, ctx.store_id, conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return {"success": True}


def _user_id(ctx: StoreContext) -> Optional[str]:
    """StoreContext сегодня не носит user_id — достаём его из того же JWT,
    которым резолвился контекст. Когда user_id появится в StoreContext,
    останется удалить эту функцию."""
    from backend.security.auth import decode_token

    payload = decode_token(ctx.token) if ctx.token else None
    return (payload or {}).get("sub")
