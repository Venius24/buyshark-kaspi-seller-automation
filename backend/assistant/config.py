"""Конфигурация ИИ-помощника. Всё из окружения — ключей в репозитории нет.

.env читается тем же минимальным парсером, что и backend/chatbot/config.py:
FastAPI-процесс грузит .env в main.py сам, но тесты и ad-hoc скрипты,
импортирующие только этот модуль, — нет.
"""

import os
import math
from dataclasses import dataclass

# Целевой провайдер — DeepSeek. Без ASSISTANT_API_KEY он всё равно
# деградирует в заглушку (см. provider.py::get_provider): помощник обязан
# работать и отвечать понятной болванкой, а не падать 500.
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_MAX_PENDING_PROPOSALS = 50


def max_pending_proposals() -> int:
    return max(1, _int_env("ASSISTANT_MAX_PENDING_PROPOSALS", DEFAULT_MAX_PENDING_PROPOSALS))


def price_gateway_name() -> str:
    value = os.environ.get("ASSISTANT_PRICE_GATEWAY", "dry_run")
    if value != "dry_run":
        raise ValueError("ASSISTANT_PRICE_GATEWAY: допустимо только dry_run; реальный шлюз отсутствует")
    return value

# Совместимые с OpenAI chat/completions эндпоинты. Оба уже используются в
# проекте: OpenAI — backend/autolist/extractor.py, DeepSeek — backend/chatbot/ai.py.
PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
}

# Ключ у помощника СВОЙ и только свой. Фоллбека на DEEPSEEK_API/OPENAI_API_KEY
# здесь сознательно нет: те ключи принадлежат чат-боту и автолистингу, и
# молча занять их значило бы (а) смешать расход по двум фичам в один счёт,
# (б) включить помощника на проде в тот момент, когда его туда выкатят, без
# отдельного решения. Не задан ASSISTANT_API_KEY — работает заглушка.
API_KEY_ENV_NAMES = ("ASSISTANT_API_KEY",)


def load_env() -> None:
    """См. backend/chatbot/config.py::load_env — тот же парсер."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_path = os.path.join(base_dir, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def _price_env(name: str):
    try:
        value = float(os.environ.get(name, ""))
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class AssistantConfig:
    enabled: bool
    provider: str
    model: str
    api_key: str
    base_url: str
    # Сколько раз подряд движок готов сходить в модель после выполнения
    # инструментов. Жёсткая граница — защита от бесконечного цикла tool-calling.
    max_tool_iterations: int
    # Сколько последних сообщений переписки уходит в модель.
    history_limit: int
    request_timeout: float
    max_output_tokens: int
    # Пока инструменты только читают. Когда появятся пишущие (изменить цену,
    # принять заказ) — они обязаны спрашивать подтверждение, см. tools.py.
    allow_write_tools: bool
    rag_ttl_seconds: int = 300
    price_prompt_per_1m: float | None = None
    price_completion_per_1m: float | None = None

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def is_stub(self) -> bool:
        return self.provider == "stub" or not self.has_api_key


def load_config() -> AssistantConfig:
    load_env()

    provider = (os.environ.get("ASSISTANT_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if provider not in PROVIDER_BASE_URLS and provider != "stub":
        provider = "stub"

    api_key = ""
    for name in API_KEY_ENV_NAMES:
        value = (os.environ.get(name) or "").strip()
        if value:
            api_key = value
            break

    return AssistantConfig(
        enabled=_bool_env("ASSISTANT_ENABLED", True),
        provider=provider,
        model=(os.environ.get("ASSISTANT_MODEL") or DEFAULT_MODEL).strip(),
        api_key=api_key,
        base_url=(os.environ.get("ASSISTANT_BASE_URL") or PROVIDER_BASE_URLS.get(provider, "")).strip(),
        max_tool_iterations=_int_env("ASSISTANT_MAX_TOOL_ITERATIONS", 4),
        history_limit=_int_env("ASSISTANT_HISTORY_LIMIT", 20),
        request_timeout=float(_int_env("ASSISTANT_TIMEOUT_SECONDS", 45)),
        max_output_tokens=_int_env("ASSISTANT_MAX_OUTPUT_TOKENS", 1200),
        allow_write_tools=_bool_env("ASSISTANT_ALLOW_WRITE_TOOLS", False),
        price_prompt_per_1m=_price_env("ASSISTANT_PRICE_PROMPT_PER_1M"),
        price_completion_per_1m=_price_env("ASSISTANT_PRICE_COMPLETION_PER_1M"),
        rag_ttl_seconds=max(1, _int_env("ASSISTANT_RAG_TTL_SECONDS", 300)),
    )


def rag_ttl_seconds() -> int:
    """TTL индекса без чтения .env при выполнении локального tool."""
    return max(1, _int_env("ASSISTANT_RAG_TTL_SECONDS", 300))
