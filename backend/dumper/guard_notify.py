"""Телеграм-уведомления защиты демпинга от оттеснения (HANDOFF §3.112).

- Продавец без рейтинга встал рядом с нами — менеджер решает кнопкой прямо
  в сообщении: не учитывать его на этом товаре или учитывать (callback
  `gdec:<id>:I|C`, обработчик — backend/chatbot/handlers.py).
- Цена за сутки упала на max_daily_drop_pct — демпинг товара на паузе.

Сбои отправки глушатся: уведомление не должно ронять цикл демпинга.
"""
import logging
import os

logger = logging.getLogger(__name__)

# Больше сообщений за один цикл не шлём — остальное ждёт в кабинете.
MAX_ASK_MESSAGES_PER_CYCLE = 5

CALLBACK_PREFIX = "gdec"


def _money(v) -> str:
    try:
        return f"{int(round(float(v))):,}".replace(",", " ") + " ₸"
    except (TypeError, ValueError):
        return "—"


def _cabinet_url() -> str:
    return os.getenv("APP_PUBLIC_URL", "https://app.buyshark.kz").rstrip("/")


def ask_keyboard(decision_id: int):
    from backend.chatbot import bot_instance
    if not bot_instance.HAS_AIOGRAM:
        return None
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚫 Не учитывать здесь", callback_data=f"{CALLBACK_PREFIX}:{decision_id}:I"),
        InlineKeyboardButton(text="✅ Учитывать", callback_data=f"{CALLBACK_PREFIX}:{decision_id}:C"),
    ]])


def ask_text(row: dict) -> str:
    from backend.chatbot.notifications import _esc_md
    return (
        "🕵️ *Продавец без рейтинга рядом с нами*\n\n"
        f"Товар: {_esc_md(row.get('product_name') or row.get('sku'))} (`{_esc_md(row.get('sku'))}`)\n"
        f"Продавец: {_esc_md(row.get('merchant_name') or row.get('merchant_id'))} — {_money(row.get('price'))}\n"
        f"Наша цена: {_money(row.get('our_price'))}\n\n"
        "Пока демпинг его учитывает. Решите, учитывать ли его на этом товаре."
    )


async def notify_new_asks(store_id, rows: list) -> None:
    if not rows:
        return
    try:
        from backend.chatbot import notify_dispatch
        for row in rows[:MAX_ASK_MESSAGES_PER_CYCLE]:
            await notify_dispatch.dispatch(
                str(store_id), "problems", ask_text(row),
                reply_markup=ask_keyboard(row["id"]),
                dedup_key=f"guard-ask-{row['id']}", card="radar",
            )
        rest = len(rows) - MAX_ASK_MESSAGES_PER_CYCLE
        if rest > 0:
            await notify_dispatch.dispatch(
                str(store_id), "problems",
                f"🕵️ И ещё {rest} продавцов без рейтинга ждут решения — "
                f"кабинет → Авто-Демпинг → «Ждут решения»: {_cabinet_url()}",
                dedup_key="guard-ask-rest", card="radar",
            )
    except Exception as e:
        logger.error("[Guard] Не удалось отправить вопрос менеджеру: %s", e)


async def notify_drop_pauses(store_id, alerts: list) -> None:
    if not alerts:
        return
    try:
        from backend.chatbot import notify_dispatch
        from backend.chatbot.notifications import _esc_md
        lines = ["📉 *Демпинг на паузе: цена падала слишком быстро*", ""]
        for a in alerts[:10]:
            lines.append(
                f"• {_esc_md(a.get('product_name') or a.get('sku'))} (`{_esc_md(a.get('sku'))}`): "
                f"{_money(a.get('drop_from'))} → {_money(a.get('price'))}"
            )
        if len(alerts) > 10:
            lines.append(f"• …и ещё {len(alerts) - 10}")
        lines += ["", "Цена держится на этом уровне, потом демпинг продолжится. "
                      "Отключить уведомление — в настройках защиты."]
        await notify_dispatch.dispatch(
            str(store_id), "problems", "\n".join(lines),
            dedup_key="guard-drop-" + ",".join(sorted(a["sku"] for a in alerts)), card="radar",
        )
    except Exception as e:
        logger.error("[Guard] Не удалось отправить тревогу о паузе: %s", e)
