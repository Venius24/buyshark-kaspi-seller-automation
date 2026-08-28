"""Суточная сводка операционного здоровья + мгновенный алерт «логин-шторма».

Раз в сутки в OPS_DIGEST_HOUR:00 (локальное время) владельцу уходит сводка
счётчиков за сутки: 429-ки Kaspi, релогины MC, пропущенные из-за cooldown
релогины, недоставленные вебхуки. Плюс раз в минуту проверяется детектор
шторма (>= OPS_STORM_THRESHOLD неудачных релогинов за окно) — тогда алерт
уходит НЕМЕДЛЕННО, а не утром.

Куда слать: OPS_DIGEST_CHAT_ID (telegram chat id владельца) из окружения.
Сообщение уходит от бота магазина (bot_instance.bot). Переменная не задана
или бота нет — сводка только в лог, ничего не падает.
"""
import asyncio
import logging
import os
from datetime import datetime

from backend.services import ops_metrics

logger = logging.getLogger(__name__)

_DIGEST_HOUR = int(os.getenv("OPS_DIGEST_HOUR", "9"))
_CHECK_INTERVAL_SECONDS = int(os.getenv("OPS_DIGEST_CHECK_SECONDS", "60"))

_LAST_DIGEST_DATE = None

_COUNTER_TITLES = {
    "kaspi_429": "429 от Kaspi (с backoff)",
    "kaspi_429_exhausted": "429 исчерпали ретраи ⚠️",
    "kaspi_http_error_5xx": "5xx от Kaspi",
    "mc_relogin_ok": "авто-релогины MC успешные",
    "mc_relogin_failed": "авто-релогины MC НЕУДАЧНЫЕ ⚠️",
    "mc_relogin_skipped_cooldown": "релогины пропущены (cooldown)",
    "webhook_fail": "вебхуки не доставлены (3 попытки) ⚠️",
}


def _bot():
    try:
        from backend.chatbot import bot_instance
        if bot_instance.bot and getattr(bot_instance, "HAS_AIOGRAM", True):
            return bot_instance.bot
    except Exception:
        pass
    return None


async def _send(text: str) -> bool:
    chat_id_raw = os.getenv("OPS_DIGEST_CHAT_ID", "").strip()
    if not chat_id_raw:
        logger.info(f"[OpsDigest] (OPS_DIGEST_CHAT_ID не задан) {text}")
        return False
    bot = _bot()
    if bot is None:
        logger.info(f"[OpsDigest] (бот недоступен) {text}")
        return False
    try:
        await bot.send_message(chat_id=int(chat_id_raw), text=text[:4000])
        return True
    except Exception as e:
        logger.error(f"[OpsDigest] Не удалось доставить сводку: {e}")
        return False


def _compose_digest(snap: dict, day_label: str) -> str:
    counters = snap.get("counters", {})
    events = snap.get("events", [])
    lines = [f"🌙 Сводка за {day_label}:"]
    if not any(counters.values()):
        lines.append("✅ Тишина: ни одного примечательного события за сутки.")
    else:
        for key, title in _COUNTER_TITLES.items():
            v = counters.get(key, 0)
            if v:
                lines.append(f"• {title}: {v}")
    if events:
        shown = events[-15:]
        lines.append("")
        lines.append("Последние события:")
        lines.extend(f"  {e}" for e in shown)
    return "\n".join(lines)


async def ops_digest_loop() -> None:
    global _LAST_DIGEST_DATE
    logger.info(
        f"📊 OpsDigest запущен: сводка в {_DIGEST_HOUR}:00, "
        f"шторм-алерт при >= {ops_metrics._STORM_THRESHOLD} фейл-релогинах за "
        f"{ops_metrics._STORM_WINDOW_SECONDS // 60} мин."
    )
    while True:
        try:
            await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
            now = datetime.now()

            storm = ops_metrics.storm_check()
            if storm.get("storm"):
                stores = ", ".join(storm.get("stores") or [])
                await _send(
                    f"🚨 ЛОГИН-ШТОРМ: {storm['fails_in_window']} неудачных авто-релогинов MC "
                    f"за последние {ops_metrics._STORM_WINDOW_SECONDS // 60} мин ({stores}).\n"
                    "Релогины сейчас подавлены cooldown — аккаунт не ддосится, но сессии не "
                    "восстанавливаются сами.\nПроверь пароль/статус магазина в кабинете."
                )

            today = now.date()
            if now.hour == _DIGEST_HOUR and _LAST_DIGEST_DATE != today:
                _LAST_DIGEST_DATE = today
                snap = ops_metrics.snapshot(reset=True)
                await _send(_compose_digest(snap, today.strftime("%d.%m.%Y")))
        except Exception as e:
            logger.error(f"[OpsDigest] Ошибка цикла: {e}")


async def start_ops_digest_loop() -> None:
    await ops_digest_loop()
