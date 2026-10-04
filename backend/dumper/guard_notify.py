"""Телеграм-уведомления защиты демпинга от оттеснения (HANDOFF §3.112).

- Продавец без рейтинга влияет на нашу цену — менеджер решает кнопкой прямо
  в сообщении: не учитывать его на этом товаре, нигде или учитывать (callback
  `gdec:<id>:I|A|C`, обработчик — backend/chatbot/handlers.py). В сообщении —
  ссылки на товар и продавца, места на карточке и цена с ним и без него.
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚫 Не учитывать здесь", callback_data=f"{CALLBACK_PREFIX}:{decision_id}:I"),
            InlineKeyboardButton(text="✅ Учитывать", callback_data=f"{CALLBACK_PREFIX}:{decision_id}:C"),
        ],
        [InlineKeyboardButton(text="⛔ Не учитывать на всех товарах",
                              callback_data=f"{CALLBACK_PREFIX}:{decision_id}:A")],
    ])


def merchant_url(merchant_id) -> str:
    return f"https://kaspi.kz/shop/info/merchant/{merchant_id}/address-tab/"


def _link(text, url) -> str:
    from backend.chatbot.notifications import _esc_md
    t = _esc_md(text).replace("]", ")")
    u = str(url or "").replace(")", "%29")
    return f"[{t}]({u})" if u.startswith("http") else t


def _rank(rank, total) -> str:
    return f"{rank}-е место из {total}" if total else f"{rank}-е место"


def ask_text(row: dict, also_on: int = 0) -> str:
    """also_on — на скольких ещё наших товарах этот продавец сейчас влияет на цену."""
    from backend.chatbot.notifications import _esc_md
    from backend.dumper import guard
    mid = str(row.get("merchant_id") or "")
    total = row.get("offers_count")
    rival = [_money(row.get("price"))]
    if row.get("rival_rank"):
        rival.append(_rank(row["rival_rank"], total))
    facts = ["нет рейтинга и отзывов"]
    age = guard.merchant_age_days(mid)
    if age is not None:
        facts.append(f"на Kaspi ~{age} дн." if age < 365 else f"на Kaspi ~{age // 365} г.")
    if row.get("delivery_days") is not None:
        facts.append(f"доставка {row['delivery_days']} дн.")
    if row.get("intercity"):
        facts.append("везёт из другого города")
    ours = [_money(row.get("our_price"))]
    if row.get("our_rank"):
        ours.append(_rank(row["our_rank"], total))
    if row.get("min_price"):
        ours.append(f"наш мин. {_money(row['min_price'])}")

    lines = [
        "🕵️ *Продавец без рейтинга влияет на нашу цену*",
        "",
        f"📦 {_link(row.get('product_name') or row.get('sku'), row.get('url'))} · `{_esc_md(row.get('sku'))}`",
        f"👤 {_link(row.get('merchant_name') or mid, merchant_url(mid) if mid.isdigit() else None)}: "
        + ", ".join(rival),
        "      " + " · ".join(facts),
    ]
    if also_on:
        lines.append(f"      ещё на {also_on} наших товарах")
    lines.append("🦈 Мы: " + ", ".join(ours))
    w, wo = row.get("price_with"), row.get("price_without")
    if w is not None and wo is not None:
        diff = float(wo) - float(w)
        lines += [
            "",
            f"Учитываем его — ставим {_money(w)}.",
            f"Не учитываем — ставим {_money(wo)} ({'+' if diff > 0 else '−'}{_money(abs(diff))}).",
        ]
    return "\n".join(lines)


async def notify_new_asks(store_id, rows: list, all_asks: list = None) -> None:
    """rows — новые вопросы (с id из БД), all_asks — все вопросы этого цикла:
    по ним считается, на скольких ещё товарах продавец влияет на цену."""
    if not rows:
        return
    try:
        from backend.chatbot import notify_dispatch
        for row in rows[:MAX_ASK_MESSAGES_PER_CYCLE]:
            also_on = len({a.get("sku") for a in all_asks or []
                           if a.get("merchant_id") == row.get("merchant_id") and a.get("sku") != row.get("sku")})
            await notify_dispatch.dispatch(
                str(store_id), "problems", ask_text(row, also_on),
                reply_markup=ask_keyboard(row["id"]),
                dedup_key=f"guard-ask-{row['id']}", card="radar",
            )
        rest = len(rows) - MAX_ASK_MESSAGES_PER_CYCLE
        if rest > 0:
            await notify_dispatch.dispatch(
                str(store_id), "problems",
                f"🕵️ И ещё {rest} вопросов о продавцах без рейтинга — "
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
