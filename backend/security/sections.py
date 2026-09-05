"""Разделы кабинета и должностные пресеты сотрудников (2026-09-05).

Зачем отдельная ось, а не ещё одно значение в `UserStore.role`.
`store_roles.ROLE_RANK` — это ЛИНЕЙНАЯ шкала «сколько можно менять»
(VIEWER < MANAGER < OWNER). Реальные должности магазина на эту шкалу не
ложатся: сборщик и чат-менеджер оба «могут менять», но менять они должны
РАЗНОЕ. Впихнуть «Сборщика» между VIEWER и MANAGER означало бы либо дать
ему чужие разделы, либо сломать смысл рангов для всего существующего кода.
Поэтому прав две независимые оси, ровно как уже сделано для платформенных
ролей (см. platform_roles.py):

  1. `UserStore.role`      — ГЛУБИНА прав: смотреть / работать / управлять.
  2. `UserStore.allowed_sections` — ШИРИНА прав: какие разделы кабинета
     вообще существуют для этого сотрудника (JSON-список ключей отсюда).

`allowed_sections = NULL` означает «ограничений нет» — это состояние всех
владельцев и всех строк, существовавших до миграции 040. Пустой список и
NULL — РАЗНЫЕ вещи: пустой список это «ничего не разрешено», и он тоже
валиден (заблокированный, но не удалённый сотрудник).

Ключи разделов дословно совпадают со значениями `ActiveTab` во фронтенде
(`frontend/src/types.ts`), чтобы гейт сайдбара был прямым отображением
этого списка, а не вторым, расходящимся с бэкендом справочником.
"""
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    # Раздел про деньги/креды/интеграции магазина — сотруднику его не выдать
    # ни в каком пресете, даже вручную. Проверяется в sanitize_sections, а не
    # только в UI: иначе владелец мог бы одним PATCH'ем открыть сборщику
    # тариф и пароли от кабинета Kaspi.
    owner_only: bool = False


SECTIONS: tuple[Section, ...] = (
    Section("overview", "Обзор"),
    Section("products", "Товары"),
    Section("dumping", "Демпинг"),
    Section("stock-groups", "Связка остатков"),
    Section("autolist", "Автодобавление товара"),
    Section("packaging", "Упаковка"),
    Section("transfer", "Передача"),
    Section("print-batcher", "Печать"),
    Section("auto-assembly", "Автосборка"),
    Section("chats", "Чаты"),
    Section("chatbot", "Бот-ответчик"),
    Section("logs", "Логи"),
    Section("help", "Помощь"),
    Section("assistant", "ИИ-помощник"),
    # Владельческое — деньги, креды магазина, подключение магазинов, API.
    Section("stores", "Магазины", owner_only=True),
    Section("global-control", "Глобальное управление", owner_only=True),
    Section("settings", "Настройки", owner_only=True),
    Section("billing", "Тариф и баланс", owner_only=True),
    Section("public-api", "API для 1С/ERP", owner_only=True),
    # Управление самими сотрудниками — рекурсивно владельческое: если это
    # отдать сотруднику, он сможет выдать себе любой другой раздел.
    Section("employees", "Сотрудники", owner_only=True),
)

SECTION_KEYS: frozenset[str] = frozenset(s.key for s in SECTIONS)
OWNER_ONLY_SECTIONS: frozenset[str] = frozenset(s.key for s in SECTIONS if s.owner_only)
EMPLOYEE_SECTIONS: frozenset[str] = SECTION_KEYS - OWNER_ONLY_SECTIONS

# Разделы, которые есть у любого сотрудника независимо от пресета: без них
# кабинет — пустой экран без единой кнопки, а «Помощь» это ещё и контакт
# поддержки, отбирать его смысла нет.
BASE_SECTIONS: frozenset[str] = frozenset({"overview", "help"})


@dataclass(frozen=True)
class JobPreset:
    code: str
    title: str
    role: str  # значение UserStore.role — глубина прав, см. store_roles.py
    sections: frozenset[str]
    hint: str


JOB_PRESETS: tuple[JobPreset, ...] = (
    JobPreset(
        code="PACKER", title="Сборщик", role="MANAGER",
        sections=BASE_SECTIONS | {"packaging", "transfer", "print-batcher"},
        hint="Собирает и печатает заказы. Не видит цены, чаты и деньги.",
    ),
    JobPreset(
        code="CHAT_MANAGER", title="Менеджер чата", role="MANAGER",
        sections=BASE_SECTIONS | {"chats", "chatbot", "products"},
        hint="Отвечает клиентам и ведёт бота-ответчика.",
    ),
    JobPreset(
        code="OPERATOR", title="Оператор", role="MANAGER",
        sections=BASE_SECTIONS | {
            "packaging", "transfer", "print-batcher", "chats", "chatbot", "products",
        },
        hint="Сборка, печать и чаты сразу. Всё кроме цен, автосборки и денег.",
    ),
    JobPreset(
        code="PRICER", title="Ценовик", role="MANAGER",
        sections=BASE_SECTIONS | {"products", "dumping", "stock-groups", "autolist"},
        hint="Ведёт цены, демпинг и остатки. Не трогает заказы и деньги.",
    ),
    JobPreset(
        code="OBSERVER", title="Наблюдатель", role="VIEWER",
        sections=BASE_SECTIONS | {"products", "packaging", "transfer", "logs"},
        hint="Только смотрит. Ничего изменить не может.",
    ),
)

PRESETS_BY_CODE: dict[str, JobPreset] = {p.code: p for p in JOB_PRESETS}

DEFAULT_PRESET_CODE = "PACKER"


def sanitize_sections(raw: Iterable[str]) -> list[str]:
    """Отбрасывает несуществующие и владельческие ключи, гарантирует базовые.

    Порядок — как в SECTIONS, чтобы одинаковый набор всегда сериализовался
    одинаково (иначе сравнение «изменилось ли» в UI и в аудите шумит)."""
    wanted = set(raw) & EMPLOYEE_SECTIONS
    wanted |= BASE_SECTIONS
    return [s.key for s in SECTIONS if s.key in wanted]


def effective_sections(role: str, allowed_sections: Optional[list[str]]) -> frozenset[str]:
    """Что реально доступно пользователю в конкретном магазине.

    OWNER игнорирует allowed_sections намеренно: владелец не может урезать
    сам себя до состояния, из которого не дотянется до раздела «Сотрудники»
    и не вернёт себе права обратно."""
    if role == "OWNER":
        return SECTION_KEYS
    if allowed_sections is None:
        # Строка до миграции 040 либо сотрудник, заведённый через Telegram —
        # ограничений по разделам нет, работает только ранг роли (как и до
        # появления этой оси). Сужать задним числом нельзя: это молча
        # отобрало бы доступ у уже работающих сотрудников.
        return EMPLOYEE_SECTIONS
    return frozenset(allowed_sections) & EMPLOYEE_SECTIONS | BASE_SECTIONS
