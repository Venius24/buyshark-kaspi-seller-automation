"""Хранение переписок помощника. Всё store-scoped: любая выборка обязательно
фильтруется по store_id, даже когда известен conversation_id — чтобы чужой
идентификатор диалога не открывал чужие данные."""

import json
import logging
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.assistant.engine import ToolInvocation
from backend.assistant.provider import ROLE_ASSISTANT, ROLE_USER, Message
from backend.db.models import AssistantConversationDB, AssistantMessageDB

logger = logging.getLogger(__name__)


async def create_conversation(db: AsyncSession, store_id: str, user_id: Optional[str], title: str = "") -> AssistantConversationDB:
    convo = AssistantConversationDB(
        store_id=store_id,
        user_id=user_id,
        title=(title or "Новый диалог").strip()[:255],
    )
    db.add(convo)
    await db.flush()
    return convo


async def list_conversations(db: AsyncSession, store_id: str, limit: int = 50) -> list[AssistantConversationDB]:
    stmt = (
        select(AssistantConversationDB)
        .where(AssistantConversationDB.store_id == store_id, AssistantConversationDB.archived.is_(False))
        .order_by(AssistantConversationDB.updated_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_conversation(db: AsyncSession, store_id: str, conversation_id: str) -> Optional[AssistantConversationDB]:
    stmt = select(AssistantConversationDB).where(
        AssistantConversationDB.id == conversation_id,
        AssistantConversationDB.store_id == store_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def archive_conversation(db: AsyncSession, store_id: str, conversation_id: str) -> bool:
    convo = await get_conversation(db, store_id, conversation_id)
    if not convo:
        return False
    convo.archived = True
    return True


async def list_messages(db: AsyncSession, store_id: str, conversation_id: str, limit: int = 200) -> list[AssistantMessageDB]:
    stmt = (
        select(AssistantMessageDB)
        .where(
            AssistantMessageDB.conversation_id == conversation_id,
            AssistantMessageDB.store_id == store_id,
        )
        .order_by(AssistantMessageDB.created_at.asc(), AssistantMessageDB.id.asc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def append_message(
    db: AsyncSession,
    store_id: str,
    conversation_id: str,
    role: str,
    content: str,
    tool_trace: Optional[list[ToolInvocation]] = None,
    provider: Optional[str] = None,
    usage: Optional[dict] = None,
) -> AssistantMessageDB:
    usage = usage or {}
    row = AssistantMessageDB(
        conversation_id=conversation_id,
        store_id=store_id,
        role=role,
        content=content or "",
        tool_trace=_dump_trace(tool_trace),
        provider=provider,
        model=usage.get("model") or None,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )
    db.add(row)
    await db.flush()
    return row


def _dump_trace(trace: Optional[list[ToolInvocation]]) -> Optional[str]:
    if not trace:
        return None
    try:
        return json.dumps(
            [{"name": t.name, "arguments": t.arguments, "result": t.result} for t in trace],
            ensure_ascii=False,
            default=str,
        )
    except (TypeError, ValueError):
        logger.warning("⚠️ Ассистент: трасса инструментов не сериализуется, сохраняем пустой")
        return None


def load_trace(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def to_llm_history(rows: list[AssistantMessageDB], limit: int) -> list[Message]:
    """Строки БД → сообщения для модели.

    В историю уходят только user/assistant: сообщения инструментов относятся к
    конкретному ходу, и их идентификаторы вызовов (tool_call_id) в следующем
    запросе уже не имеют пары — модель на них подавится. Что именно помощник
    смотрел, остаётся в tool_trace для UI и отладки.
    """
    history: list[Message] = []
    for row in rows[-limit:] if limit > 0 else rows:
        if row.role not in (ROLE_USER, ROLE_ASSISTANT):
            continue
        if not (row.content or "").strip():
            continue
        history.append(Message(role=row.role, content=row.content))
    return history


async def count_messages(db: AsyncSession, store_id: str, conversation_id: str) -> int:
    stmt = select(func.count()).select_from(AssistantMessageDB).where(
        AssistantMessageDB.conversation_id == conversation_id,
        AssistantMessageDB.store_id == store_id,
    )
    return int((await db.execute(stmt)).scalar() or 0)
