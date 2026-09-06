"""Движок помощника: цикл «модель → инструменты → модель».

Ничего не знает ни про HTTP, ни про БД, ни про конкретного провайдера — на
вход история и вопрос, на выход ответ и трасса вызовов. Благодаря этому
тестируется подставным провайдером без сети (tests/test_assistant_skeleton.py).
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.assistant import builtin_tools  # noqa: F401 — регистрирует инструменты в реестре
from backend.assistant import tools as tool_registry
from backend.assistant.config import AssistantConfig, load_config
from backend.assistant.prompt import StoreBrief, build_system_prompt
from backend.assistant.provider import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    LLMProvider,
    Message,
    get_provider,
)
from backend.assistant.tools import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class ToolInvocation:
    """Одна запись трассы — что модель позвала и что получила."""
    name: str
    arguments: dict
    result: dict


@dataclass
class AssistantTurn:
    reply: str
    tool_trace: list[ToolInvocation] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    provider: str = ""
    error: Optional[str] = None
    # Модель упёрлась в лимит итераций tool-calling — ответ может быть неполным.
    truncated: bool = False


async def run_turn(
    user_text: str,
    history: list[Message],
    brief: StoreBrief,
    ctx: ToolContext,
    cfg: Optional[AssistantConfig] = None,
    provider: Optional[LLMProvider] = None,
) -> AssistantTurn:
    """Один ход диалога.

    history — предыдущие сообщения БЕЗ систем-промпта и без текущего вопроса;
    их подрезает вызывающий (storage.py отдаёт последние cfg.history_limit).
    """
    cfg = cfg or load_config()
    provider = provider or get_provider(cfg)

    specs = tool_registry.available_specs(ctx, allow_write=cfg.allow_write_tools)
    wire_tools = tool_registry.wire_schema(specs)

    messages: list[Message] = [Message(role=ROLE_SYSTEM, content=build_system_prompt(brief))]
    messages += list(history)
    messages.append(Message(role=ROLE_USER, content=user_text))

    trace: list[ToolInvocation] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "model": ""}

    for iteration in range(cfg.max_tool_iterations + 1):
        result = await provider.complete(messages, tools=wire_tools or None)

        if result.usage:
            usage_total["model"] = result.usage.get("model") or usage_total["model"]
            usage_total["prompt_tokens"] += result.usage.get("prompt_tokens") or 0
            usage_total["completion_tokens"] += result.usage.get("completion_tokens") or 0

        if result.error:
            return AssistantTurn(
                reply="",
                tool_trace=trace,
                usage=usage_total,
                provider=provider.name,
                error=result.error,
            )

        if not result.tool_calls:
            return AssistantTurn(
                reply=result.text,
                tool_trace=trace,
                usage=usage_total,
                provider=provider.name,
            )

        # Последняя итерация исчерпана, а модель всё ещё зовёт инструменты —
        # прекращаем, чтобы не крутиться бесконечно.
        if iteration >= cfg.max_tool_iterations:
            logger.warning(f"⚠️ Ассистент: достигнут лимит итераций tool-calling ({cfg.max_tool_iterations})")
            return AssistantTurn(
                reply=result.text or "Не удалось завершить ответ: слишком много обращений к данным подряд.",
                tool_trace=trace,
                usage=usage_total,
                provider=provider.name,
                truncated=True,
            )

        messages.append(Message(role=ROLE_ASSISTANT, content=result.text, tool_calls=result.tool_calls))
        for call in result.tool_calls:
            payload = await tool_registry.execute(call.name, call.arguments, ctx, allow_write=cfg.allow_write_tools)
            trace.append(ToolInvocation(name=call.name, arguments=call.arguments, result=payload))
            messages.append(
                Message(
                    role=ROLE_TOOL,
                    content=tool_registry.serialize_result(payload),
                    tool_call_id=call.id,
                    name=call.name,
                )
            )

    # Недостижимо: цикл всегда выходит через return выше.
    return AssistantTurn(reply="", tool_trace=trace, usage=usage_total, provider=provider.name, error="Внутренняя ошибка движка")
