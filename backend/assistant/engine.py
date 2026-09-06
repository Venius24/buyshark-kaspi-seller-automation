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

# Что говорим модели, когда бюджет вызовов инструментов исчерпан.
FORCE_ANSWER_PROMPT = (
    "Лимит обращений к данным исчерпан. Ответь пользователю ПРЯМО СЕЙЧАС, "
    "опираясь только на уже полученные результаты инструментов. Инструменты "
    "больше не доступны — не обещай ничего искать и не проси подождать. "
    "Если собранного не хватает для полного ответа, дай то, что есть, и одной "
    "строкой скажи, чего не хватило и как переформулировать вопрос."
)


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

    def _account(result) -> None:
        if result.usage:
            usage_total["model"] = result.usage.get("model") or usage_total["model"]
            usage_total["prompt_tokens"] += result.usage.get("prompt_tokens") or 0
            usage_total["completion_tokens"] += result.usage.get("completion_tokens") or 0

    truncated = False

    for iteration in range(cfg.max_tool_iterations + 1):
        result = await provider.complete(messages, tools=wire_tools or None)
        _account(result)

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

        if iteration >= cfg.max_tool_iterations:
            truncated = True
            break

    # Бюджет итераций исчерпан, а модель всё ещё хочет звать инструменты.
    #
    # Раньше здесь возвращался `result.text` последнего хода — а это текст,
    # который модель написала ВМЕСТЕ с вызовом инструментов, то есть обрывок
    # плана вида «сейчас поищу ещё пару категорий». Пользователь видел
    # незаконченную мысль вместо ответа (прод, 2026-09-06: вопрос «какие
    # товары скоро закончатся» → 11 вызовов search_products и ни одной цифры).
    #
    # Теперь делаем ещё один ход БЕЗ инструментов: у модели уже есть всё, что
    # она успела собрать, и единственное, что ей остаётся — ответить.
    logger.warning(f"⚠️ Ассистент: лимит итераций tool-calling ({cfg.max_tool_iterations}), добираю ответ без инструментов")
    messages.append(Message(role=ROLE_SYSTEM, content=FORCE_ANSWER_PROMPT))
    final = await provider.complete(messages, tools=None)
    _account(final)

    if final.error or not final.text:
        return AssistantTurn(
            reply=(
                "Не удалось собрать ответ: запрос потребовал слишком много обращений к данным. "
                "Попробуй спросить конкретнее — например, назови категорию или артикул."
            ),
            tool_trace=trace,
            usage=usage_total,
            provider=provider.name,
            truncated=True,
        )

    return AssistantTurn(
        reply=final.text,
        tool_trace=trace,
        usage=usage_total,
        provider=provider.name,
        truncated=truncated,
    )
