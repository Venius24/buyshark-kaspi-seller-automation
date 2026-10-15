"""Реестр инструментов ИИ-помощника.

Инструмент = функция, которую модель может позвать сама. Каркасные правила,
заложенные здесь намеренно:

1. **Инструмент всегда получает ToolContext** — store_id/user_id/роль
   запросившего. Ни один инструмент не имеет права ходить в чужой магазин:
   store_id берётся ИЗ КОНТЕКСТА, а не из аргументов модели. Это тот же
   принцип, что и во всём остальном бэкенде после Phase 4 (см.
   backend/security/store_context.py).
2. **Пишущие инструменты по умолчанию выключены** (`read_only=False` +
   ASSISTANT_ALLOW_WRITE_TOOLS=0) и должны требовать явного подтверждения
   пользователя. Модель не меняет цены и не принимает заказы молча.
3. **Схема аргументов — JSON Schema**, отдаётся модели как есть.
4. **Инструмент наследует права раздела** (`section=`). Помощник не должен
   становиться обходным путём вокруг прав сотрудника: если сборщику не выдали
   раздел «Товары», он не должен узнавать закупочные цены, спросив ассистента.
"""

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Хендлер: (arguments, ctx) -> результат (сериализуемый в JSON).
ToolHandler = Callable[[dict, "ToolContext"], Any | Awaitable[Any]]


@dataclass
class ToolContext:
    """Кто спрашивает. Заполняется роутером из StoreContext."""
    store_id: str
    user_id: Optional[str] = None
    role: str = "OWNER"
    # Пользователь ещё не подтвердил изменение — пишущие инструменты обязаны
    # вернуть "нужно подтверждение", а не выполниться.
    confirmed: bool = False
    # Токен Kaspi API этого магазина (StoreContext.token). Нужен инструментам,
    # которые читают заказы/каталог: половина данных кабинета живёт не в нашей
    # БД, а в Kaspi, и достаётся только по токену магазина.
    token: str = ""
    # Разделы кабинета, доступные пользователю (StoreContext.sections, вторая
    # ось прав — см. backend/security/sections.py). None = ограничений нет.
    sections: Optional[frozenset[str]] = None
    # Только серверная DB-сессия и проверенный роутером диалог.
    db: Any = None
    conversation_id: Optional[str] = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema объекта аргументов
    handler: ToolHandler
    read_only: bool = True
    # Роль в магазине, ниже которой инструмент недоступен (OWNER > MANAGER > VIEWER).
    min_role: str = "VIEWER"
    # Раздел кабинета, к данным которого инструмент даёт доступ. Помощник не
    # должен становиться обходным путём вокруг прав сотрудника: если сборщику
    # не выдали раздел "products", он не должен спрашивать цены у ассистента и
    # получать их. None = раздел не требуется.
    section: Optional[str] = None

    def to_wire(self) -> dict:
        """В формат tools[] OpenAI chat/completions."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, ToolSpec] = {}

_ROLE_ORDER = {"VIEWER": 0, "MANAGER": 1, "OWNER": 2}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"Инструмент '{spec.name}' уже зарегистрирован")
    _REGISTRY[spec.name] = spec
    return spec


def tool(
    name: str,
    description: str,
    parameters: Optional[dict] = None,
    read_only: bool = True,
    min_role: str = "VIEWER",
    section: Optional[str] = None,
):
    """Декоратор регистрации: см. backend/assistant/builtin_tools.py."""

    def wrapper(fn: ToolHandler) -> ToolHandler:
        register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters or {"type": "object", "properties": {}, "additionalProperties": False},
                handler=fn,
                read_only=read_only,
                min_role=min_role,
                section=section,
            )
        )
        return fn

    return wrapper


def get(name: str) -> Optional[ToolSpec]:
    return _REGISTRY.get(name)


def all_specs() -> list[ToolSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: s.name)


def section_allowed(ctx: ToolContext, spec: ToolSpec) -> bool:
    """Открыт ли пользователю раздел, данные которого отдаёт инструмент.

    ctx.sections=None означает «ограничений нет» (владелец либо строка до
    миграции 040) — ровно та же трактовка, что и в security/sections.py.
    Пустое множество — «не разрешено ничего», и это валидное состояние."""
    if not spec.section:
        return True
    if ctx.sections is None:
        return True
    return spec.section in ctx.sections


def available_specs(ctx: ToolContext, allow_write: bool) -> list[ToolSpec]:
    """Что реально показываем модели для этого пользователя."""
    user_rank = _ROLE_ORDER.get((ctx.role or "VIEWER").upper(), 0)
    result = []
    for spec in all_specs():
        if not spec.read_only and not allow_write:
            continue
        if user_rank < _ROLE_ORDER.get(spec.min_role.upper(), 0):
            continue
        if not section_allowed(ctx, spec):
            continue
        result.append(spec)
    return result


def wire_schema(specs: list[ToolSpec]) -> list[dict]:
    return [s.to_wire() for s in specs]


async def execute(name: str, arguments: dict, ctx: ToolContext, allow_write: bool = False) -> dict:
    """Выполняет инструмент. Никогда не бросает наружу — ошибка возвращается
    модели текстом, чтобы она могла исправиться на следующей итерации."""
    spec = _REGISTRY.get(name)
    if not spec:
        return {"ok": False, "error": f"Инструмент '{name}' не существует"}

    user_rank = _ROLE_ORDER.get((ctx.role or "VIEWER").upper(), 0)
    if user_rank < _ROLE_ORDER.get(spec.min_role.upper(), 0):
        return {"ok": False, "error": f"Недостаточно прав в магазине для '{name}' (нужна роль {spec.min_role})"}

    # Проверяем ещё раз, а не полагаемся на то, что available_specs не показал
    # инструмент: показанный список — это подсказка модели, а не граница прав.
    # Модель может позвать имя, которое увидела в истории диалога или выдумала.
    if not section_allowed(ctx, spec):
        return {"ok": False, "error": f"Раздел '{spec.section}' не открыт этому пользователю — данные недоступны"}

    if not spec.read_only:
        if not allow_write:
            return {"ok": False, "error": f"Изменяющие инструменты выключены (ASSISTANT_ALLOW_WRITE_TOOLS=0): '{name}'"}
        if not ctx.confirmed:
            return {"ok": False, "error": f"Действие '{name}' меняет данные магазина и требует подтверждения пользователя"}

    try:
        result = spec.handler(arguments or {}, ctx)
        if inspect.isawaitable(result):
            result = await result
        return {"ok": True, "result": result}
    except Exception as e:
        logger.exception(f"💥 Ассистент: инструмент '{name}' упал")
        return {"ok": False, "error": f"Инструмент '{name}' завершился ошибкой: {e}"}


def serialize_result(payload: dict) -> str:
    """Результат инструмента → строка content для сообщения role=tool."""
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "error": "Результат не сериализуется в JSON"}, ensure_ascii=False)


def _reset_for_tests() -> None:
    """Только для тестов — вычистить реестр."""
    _REGISTRY.clear()
