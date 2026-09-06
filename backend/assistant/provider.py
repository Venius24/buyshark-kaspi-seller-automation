"""Абстракция над LLM для помощника.

Один интерфейс — две реализации:

- StubProvider — ничего не знает про сеть и ключи. Работает всегда, отвечает
  честной болванкой. На нём живёт весь каркас, пока ключа нет.
- OpenAICompatibleProvider — сырой httpx к chat/completions с tool-calling.
  Формат общий у OpenAI и DeepSeek, поэтому одного класса хватает обоим.
  SDK не тянем — в проекте уже принято ходить в LLM напрямую httpx
  (backend/chatbot/ai.py, backend/autolist/extractor.py).

Движок (engine.py) знает ТОЛЬКО про этот интерфейс.
"""

import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from backend.assistant.config import API_KEY_ENV_NAMES, AssistantConfig, load_config

logger = logging.getLogger(__name__)

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass
class ToolCall:
    """Запрос модели на вызов инструмента."""
    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    """Одно сообщение диалога в терминах модели."""
    role: str
    content: str = ""
    # Заполнено только у role=assistant, когда модель просит инструменты.
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Заполнено только у role=tool — к какому вызову это результат.
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_wire(self) -> dict:
        """В формат OpenAI chat/completions."""
        if self.role == ROLE_TOOL:
            return {
                "role": ROLE_TOOL,
                "tool_call_id": self.tool_call_id,
                "content": self.content,
            }
        payload: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in self.tool_calls
            ]
        return payload


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # {"model", "prompt_tokens", "completion_tokens"} — по образцу
    # chatbot/ai.py, чтобы позже подключить к тарификации.
    usage: dict = field(default_factory=dict)
    # Провайдер не смог ответить (нет ключа, сеть, HTTP-ошибка) — движок
    # отдаёт это наружу как есть, а не молча выдумывает ответ.
    error: Optional[str] = None


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def complete(self, messages: list[Message], tools: Optional[list[dict]] = None) -> LLMResult:
        ...


class StubProvider(LLMProvider):
    """Заглушка на время каркаса: не ходит в сеть, не требует ключа.

    Отвечает предсказуемым текстом с эхом последнего вопроса — этого хватает,
    чтобы прогнать весь путь (UI → роутер → движок → БД) до появления ключа,
    и чтобы тесты не зависели от внешнего сервиса.
    """

    name = "stub"

    def __init__(self, reason: str = ""):
        self.reason = reason

    async def complete(self, messages: list[Message], tools: Optional[list[dict]] = None) -> LLMResult:
        last_user = next((m.content for m in reversed(messages) if m.role == ROLE_USER), "")
        tool_names = ", ".join(t.get("function", {}).get("name", "?") for t in (tools or [])) or "—"
        text = (
            "🤖 ИИ-помощник пока работает в режиме каркаса: языковая модель не подключена.\n\n"
            f"Ваш вопрос: «{last_user.strip()[:400]}»\n\n"
            f"Доступные инструменты (объявлены, ещё не реализованы): {tool_names}."
        )
        if self.reason:
            text += f"\n\nПричина режима заглушки: {self.reason}"
        return LLMResult(text=text, usage={"model": self.name, "prompt_tokens": 0, "completion_tokens": 0})


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-совместимый chat/completions с tool-calling (OpenAI, DeepSeek)."""

    def __init__(self, cfg: AssistantConfig):
        self.name = cfg.provider
        self.cfg = cfg

    async def complete(self, messages: list[Message], tools: Optional[list[dict]] = None) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [m.to_wire() for m in messages],
            "temperature": 0.2,
            "max_tokens": self.cfg.max_output_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.cfg.request_timeout) as client:
                resp = await client.post(self.cfg.base_url, headers=headers, json=payload)
        except Exception as e:  # сеть/таймаут — наружу понятной ошибкой, не 500
            logger.error(f"💥 Ассистент: сбой вызова {self.name}: {e}")
            return LLMResult(error=f"Не удалось обратиться к модели ({self.name}): {e}")

        if resp.status_code != 200:
            logger.error(f"💥 Ассистент: HTTP {resp.status_code} от {self.name}: {resp.text[:500]}")
            return LLMResult(error=f"Модель вернула HTTP {resp.status_code}.")

        return self._parse(resp.json())

    def _parse(self, body: dict) -> LLMResult:
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}

        tool_calls: list[ToolCall] = []
        for raw in msg.get("tool_calls") or []:
            fn = raw.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                # Модель иногда отдаёт битый JSON в аргументах — не роняем
                # весь ход, инструмент сам ответит ошибкой валидации.
                logger.warning(f"⚠️ Ассистент: невалидный JSON в аргументах инструмента: {raw_args[:200]}")
                args = {}
            tool_calls.append(ToolCall(id=raw.get("id") or f"call_{uuid.uuid4().hex[:8]}", name=fn.get("name") or "", arguments=args))

        usage_raw = body.get("usage") or {}
        return LLMResult(
            text=(msg.get("content") or "").strip(),
            tool_calls=tool_calls,
            usage={
                "model": body.get("model") or self.cfg.model,
                "prompt_tokens": usage_raw.get("prompt_tokens"),
                "completion_tokens": usage_raw.get("completion_tokens"),
            },
        )


def get_provider(cfg: Optional[AssistantConfig] = None) -> LLMProvider:
    """Фабрика. Нет ключа или провайдер=stub — отдаём заглушку, не ошибку."""
    cfg = cfg or load_config()
    if cfg.provider == "stub":
        return StubProvider(reason="ASSISTANT_PROVIDER=stub")
    if not cfg.has_api_key:
        # Только ASSISTANT_API_KEY: ключи чат-бота и автолистинга помощник
        # сознательно не занимает (см. config.API_KEY_ENV_NAMES), поэтому и
        # называть их здесь нельзя — по такой подсказке ключ положат не туда.
        return StubProvider(reason=f"ключ не задан (ожидается {', '.join(API_KEY_ENV_NAMES)})")
    if not cfg.base_url:
        return StubProvider(reason=f"не задан ASSISTANT_BASE_URL для провайдера {cfg.provider}")
    return OpenAICompatibleProvider(cfg)
