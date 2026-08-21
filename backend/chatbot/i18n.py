"""Частичная локализация интерфейса бота (Ф5, docs/TELEGRAM_BOT_PLAN.md §7.6:
"Интерфейс бота логично сразу делать двуязычным"). НЕ полный охват — только
подписи главного меню и пара ключевых сообщений (приветствие, итог
Утреннего сбора); остальные экраны остаются на русском. Язык берётся из
Telegram (language_code на /start), не настраивается вручную в этой версии."""
from typing import Set

_SUPPORTED = ("ru", "kk")

BUTTON_LABELS = {
    "morning": {"ru": "☀️ Утренний сбор", "kk": "☀️ Таңғы жинау"},
    "print": {"ru": "🖨 Печать", "kk": "🖨 Басып шығару"},
    "assembly": {"ru": "⚙️ Автосборка", "kk": "⚙️ Авто-жинау"},
    "chats": {"ru": "💬 Чаты", "kk": "💬 Чаттар"},
    "store": {"ru": "📊 Магазин", "kk": "📊 Дүкен"},
    "status": {"ru": "📊 Статус", "kk": "📊 Күй"},
    "settings": {"ru": "⚙️ Настройки", "kk": "⚙️ Баптаулар"},
}


def resolve_lang(language_code: str) -> str:
    return language_code if language_code in _SUPPORTED else "ru"


def label(key: str, lang: str) -> str:
    variants = BUTTON_LABELS.get(key)
    if not variants:
        return key
    return variants.get(resolve_lang(lang), variants["ru"])


def all_variants(key: str) -> Set[str]:
    return set(BUTTON_LABELS.get(key, {}).values())


def all_menu_labels() -> Set[str]:
    """Все подписи кнопок главного меню на всех языках — используется, чтобы
    не спутать нажатие меню с реальным ответом клиенту в режиме «✍️ Ответить»
    (см. handle_chat_reply в handlers.py)."""
    return {label for variants in BUTTON_LABELS.values() for label in variants.values()}
