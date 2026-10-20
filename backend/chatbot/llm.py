"""Вызов LLM для ИИ-автоответчика: конфиг, таймауты, ретраи, structured output, трасса.

Раньше всё это было зашито в ai.generate_ai_reply: модель, URL и таймаут —
константами, ретраев не было (один сбой DeepSeek = эскалация менеджеру),
вызов менеджера определялся подстрокой «[HUMAN_OVERRIDE]» в тексте ответа,
а латентность и стоимость не сохранялись.

Теперь:
* параметры — переменные окружения CHATBOT_LLM_* (см. load_llm_config);
* сетевые сбои, 429 и 5xx повторяются с экспоненциальной паузой, но в пределах
  общего дедлайна: воркер обрабатывает чаты последовательно, и одна зависшая
  генерация не должна держать остальные;
* structured output через вызов функции send_reply (tool_choice принудительный):
  {"reply", "action", "handoff_reason"}, action — answer / clarify / safe_unknown /
  escalate (решение владельца 2026-10-20: уточнить или честно сказать «не знаем»
  без менеджера; менеджер — только escalate), а не метка в тексте. JSON mode (response_format=json_object) не подошёл:
  deepseek-chat (на 2026-10 это алиас deepseek-flash) в нём часто возвращал
  одни пробелы — 59 из 297 вызовов eval, см. evals/autoresponder/METRICS_LOG.md.
  Если аргументы не разобрались, ответ разбирается по-старому (метка в тексте),
  в трассе это structured="fallback";
* трасса каждого вызова: модель, токены, латентность, число попыток, стоимость
  (если заданы цены), режим разбора, версия промпта. Пишется в chat_response_logs
  и в метрики Prometheus.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

HUMAN_OVERRIDE_MARK = "[HUMAN_OVERRIDE]"

# Добавляется в конец систем-промпта в structured-режиме.
STRUCTURED_INSTRUCTIONS = (
    "\n\n📤 ФОРМАТ ОТВЕТА: ответь вызовом функции send_reply. Поле action — что ты делаешь:\n"
    "- answer — отвечаешь по данным и правилам магазина.\n"
    "- clarify — обычный вопрос, но непонятно, о каком товаре или заказе речь: задай один короткий "
    "уточняющий вопрос. Менеджер не нужен. Если есть причина для escalate — выбирай escalate, не clarify. "
    "Спрашивай название товара, не ссылку (ссылки ты не открываешь), и ничего не обещай «посмотреть» или «проверить»: "
    "просто задай вопрос.\n"
    "- safe_unknown — вопрос понятен, но ответа не знает и сам магазин (курьер, сроки и адрес доставки Kaspi, "
    "то, что решает Kaspi): честно скажи и подскажи, куда обратиться (Kaspi Гид). Менеджер не нужен.\n"
    f"- escalate — нужен сотрудник: везде, где правила выше велят поставить метку {HUMAN_OVERRIDE_MARK} "
    "(в том числе характеристик или наличия нет в данных — их знает магазин). Саму метку в reply не пиши.\n"
    "- В reply — только текст для клиента, на его языке. При action=escalate reply клиенту не уйдёт: "
    "это черновик для менеджера.\n"
)

ACTIONS = ("answer", "clarify", "safe_unknown", "escalate")

REPLY_TOOL = {
    "type": "function",
    "function": {
        "name": "send_reply",
        "description": "Отправить ответ клиенту или передать чат менеджеру магазина.",
        "parameters": {
            "type": "object",
            "properties": {
                "reply": {"type": "string", "description": "Текст сообщения клиенту на его языке."},
                "action": {"type": "string", "enum": list(ACTIONS),
                           "description": "answer / clarify / safe_unknown / escalate (нужен менеджер)."},
                "handoff_reason": {"type": "string",
                                   "description": "Кратко, почему нужен менеджер; пустая строка, если action не escalate."},
            },
            "required": ["reply", "action", "handoff_reason"],
            "additionalProperties": False,
        },
    },
}


def _env_float(name: str, default: Optional[float]) -> Optional[float]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"{name}={raw!r} не число, беру {default}")
        return default


def _env_int(name: str, default: int) -> int:
    value = _env_float(name, None)
    return default if value is None else int(value)


@dataclass(frozen=True)
class LLMConfig:
    url: str = "https://api.deepseek.com/chat/completions"
    model: str = "deepseek-chat"
    temperature: float = 0.2
    timeout_s: float = 20.0
    max_retries: int = 2
    deadline_s: float = 45.0
    structured: bool = True
    # USD за 1M токенов; не заданы — стоимость в трассе None (тарифы провайдера меняются,
    # угадывать их в коде нельзя).
    price_prompt_per_1m: Optional[float] = None
    price_completion_per_1m: Optional[float] = None


def load_llm_config() -> LLMConfig:
    return LLMConfig(
        url=(os.environ.get("CHATBOT_LLM_URL") or LLMConfig.url).strip(),
        model=(os.environ.get("CHATBOT_LLM_MODEL") or LLMConfig.model).strip(),
        temperature=_env_float("CHATBOT_LLM_TEMPERATURE", LLMConfig.temperature),
        timeout_s=_env_float("CHATBOT_LLM_TIMEOUT_S", LLMConfig.timeout_s),
        max_retries=_env_int("CHATBOT_LLM_MAX_RETRIES", LLMConfig.max_retries),
        deadline_s=_env_float("CHATBOT_LLM_DEADLINE_S", LLMConfig.deadline_s),
        structured=(os.environ.get("CHATBOT_LLM_STRUCTURED", "1").strip() != "0"),
        price_prompt_per_1m=_env_float("CHATBOT_LLM_PRICE_PROMPT_PER_1M", None),
        price_completion_per_1m=_env_float("CHATBOT_LLM_PRICE_COMPLETION_PER_1M", None),
    )


@dataclass
class LLMResult:
    ok: bool
    reply: str = ""
    handoff: bool = False
    action: str = ""  # answer / clarify / safe_unknown / escalate; в text-режиме — answer или escalate
    handoff_reason: str = ""
    trace: dict = field(default_factory=dict)


def parse_model_output(content: str, structured: bool) -> tuple[str, str, str, str]:
    """-> (reply, action, handoff_reason, режим разбора: "json" | "fallback" | "text").

    action из ACTIONS; вызов менеджера — action == "escalate". Старый формат
    {"handoff": true} тоже понимается. Метка [HUMAN_OVERRIDE] в тексте — вызов
    менеджера в любом режиме: модель иногда пишет её по старой памяти и внутри JSON."""
    content = (content or "").strip()
    mode = "text"
    reply, action, reason = content, "answer", ""
    if structured:
        mode = "fallback"
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("reply"), str):
            mode = "json"
            reply = data["reply"].strip()
            raw_action = str(data.get("action") or "").strip().lower()
            if raw_action in ACTIONS:
                action = raw_action
            elif data.get("handoff") is True or str(data.get("handoff")).lower() == "true":
                action = "escalate"
            reason = str(data.get("handoff_reason") or "").strip()
    if HUMAN_OVERRIDE_MARK in reply:
        reply = reply.replace(HUMAN_OVERRIDE_MARK, "").strip()
        action = "escalate"
    return reply, action, reason[:120], mode


def cost_usd(cfg: LLMConfig, prompt_tokens, completion_tokens) -> Optional[float]:
    if cfg.price_prompt_per_1m is None or cfg.price_completion_per_1m is None or prompt_tokens is None:
        return None
    return round((prompt_tokens * cfg.price_prompt_per_1m + (completion_tokens or 0) * cfg.price_completion_per_1m) / 1e6, 8)


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


async def complete(messages: list, api_key: str, cfg: Optional[LLMConfig] = None,
                   client_factory=None) -> LLMResult:
    """Один ответ модели с ретраями. Никогда не бросает исключений: сбой — ok=False и trace["error"].
    client_factory — для тестов и раннера воспроизведения (по умолчанию httpx.AsyncClient)."""
    cfg = cfg or load_llm_config()
    client_factory = client_factory or httpx.AsyncClient
    payload = {"model": cfg.model, "messages": messages, "temperature": cfg.temperature}
    if cfg.structured:
        payload["tools"] = [REPLY_TOOL]
        payload["tool_choice"] = {"type": "function", "function": {"name": "send_reply"}}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    started = time.monotonic()
    trace = {"model": cfg.model, "attempts": 0, "structured": "off"}
    error = None

    for attempt in range(1, cfg.max_retries + 2):
        remaining = cfg.deadline_s - (time.monotonic() - started)
        if remaining <= 1:
            error = error or "дедлайн исчерпан"
            break
        trace["attempts"] = attempt
        try:
            async with client_factory(timeout=min(cfg.timeout_s, remaining)) as client:
                resp = await client.post(cfg.url, headers=headers, json=payload)
            if resp.status_code == 200:
                body = resp.json()
                choice = body["choices"][0]
                message = choice.get("message") or {}
                content = message.get("content") or ""
                for call in message.get("tool_calls") or []:
                    if (call.get("function") or {}).get("name") == "send_reply":
                        content = call["function"].get("arguments") or content
                        break
                usage = body.get("usage") or {}
                if not content.strip():
                    error = "пустой ответ модели"  # бывает в JSON mode — повторяем
                else:
                    reply, action, reason, mode = parse_model_output(content, cfg.structured)
                    trace.update(
                        prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                        latency_ms=int((time.monotonic() - started) * 1000), structured=mode, action=action,
                        finish_reason=choice.get("finish_reason"),
                        cost_usd=cost_usd(cfg, usage.get("prompt_tokens"), usage.get("completion_tokens")),
                    )
                    if not reply:
                        return LLMResult(ok=False, trace={**trace, "error": "пустой reply"})
                    return LLMResult(ok=True, reply=reply, handoff=action == "escalate", action=action,
                                     handoff_reason=reason, trace=trace)
            else:
                error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if not _retryable(resp.status_code):
                    break
        except (httpx.TimeoutException, httpx.TransportError) as e:
            error = f"{type(e).__name__}: {e}"
        except (ValueError, KeyError, IndexError) as e:  # битый JSON ответа API
            error = f"bad response: {type(e).__name__}: {e}"
        if attempt <= cfg.max_retries:
            pause = min(4.0, 0.5 * 2 ** (attempt - 1)) + random.random() * 0.3
            if cfg.deadline_s - (time.monotonic() - started) > pause + 1:
                await asyncio.sleep(pause)

    trace.update(latency_ms=int((time.monotonic() - started) * 1000), error=(error or "неизвестная ошибка")[:200])
    logger.error(f"💥 Сбой LLM автоответчика после {trace['attempts']} попыток: {trace['error']}")
    return LLMResult(ok=False, trace=trace)
