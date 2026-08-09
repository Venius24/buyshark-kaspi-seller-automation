"""Центральный диспетчер персонализированных уведомлений (Ф4,
docs/TELEGRAM_BOT_PLAN.md §6): резолвит получателей магазина через
UserStore+TelegramLinkDB, применяет персональные настройки группы (chats/
problems/money), тихие часы и батчинг для money-группы. Дедуп — простой
in-memory TTL по dedup_key (переживает рестарт хуже персистентного варианта,
но это тот же компромисс, что и PENDING_FILES в handlers.py, — см. HANDOFF).

С 2026-08-09 (см. HANDOFF) это единственный путь доставки: легаси
admin_ids-фоллбек вырезан из notifications.py, все алерты идут только
реальным привязанным через deep-link пользователям магазина."""
import asyncio
import datetime
import logging
from pathlib import Path
from typing import Optional, List

from backend.chatbot import bot_instance, notification_prefs

logger = logging.getLogger(__name__)

_CARDS_DIR = Path(__file__).parent / "assets" / "notification_cards"
CARD_FILES = {
    "welcome": _CARDS_DIR / "welcome.png",
    "new_order": _CARDS_DIR / "new_order.png",
    "dumping": _CARDS_DIR / "dumping.png",
    "money_digest": _CARDS_DIR / "money_digest.png",
    "chat_alert": _CARDS_DIR / "chat_alert.png",
    "cookie_expired": _CARDS_DIR / "cookie_expired.png",
    "assembly_failed": _CARDS_DIR / "assembly_failed.png",
    "startup": _CARDS_DIR / "startup.png",
}

_dedup_sent: dict = {}  # (store_id, dedup_key) -> datetime отправки
_DEDUP_WINDOW = datetime.timedelta(minutes=10)

_quiet_queue: dict = {}  # (store_id, tg_user_id) -> [text, ...]
_money_buffer: dict = {}  # (store_id, tg_user_id) -> {"lines": [...], "first_at": datetime}
_MONEY_DEBOUNCE = datetime.timedelta(seconds=60)

_last_evening_summary: dict = {}  # store_id -> date

EVENING_SUMMARY_HOUR = 20


def _recipients_for_store(store_id: str) -> List[int]:
    """Все привязанные Telegram-аккаунты людей, состоящих в этом магазине
    (любая роль) — независимо от того, какой стор у них сейчас "активный"
    (/switch), уведомления магазина должны доходить всегда."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from sqlalchemy import select
        from backend.db.models import UserStore, TelegramLinkDB
        rows = session.execute(
            select(TelegramLinkDB.tg_user_id)
            .join(UserStore, UserStore.user_id == TelegramLinkDB.user_id)
            .where(UserStore.store_id == store_id)
        ).all()
        return [r[0] for r in rows]
    finally:
        session.close()


def _in_quiet_hours(quiet_from: Optional[str], quiet_to: Optional[str], now: datetime.datetime) -> bool:
    if not quiet_from or not quiet_to:
        return False
    try:
        fh, fm = (int(x) for x in quiet_from.split(":"))
        th, tm = (int(x) for x in quiet_to.split(":"))
    except Exception:
        return False
    start = now.replace(hour=fh, minute=fm, second=0, microsecond=0)
    end = now.replace(hour=th, minute=tm, second=0, microsecond=0)
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # окно через полночь, напр. 23:00–08:00


_CAPTION_LIMIT = 1024  # лимит Telegram на caption у фото, у текста сообщения — 4096


async def send_card(tg_user_id: int, card: str):
    """Шлёт Higgsfield-карточку картинкой без подписи — используется там, где
    текст алерта неизвестен на месте вызова (легаси-фоллбек лупы в
    notifications.py собирают текст отдельно). Предпочитай send_with_card,
    который клеит текст в подпись одним сообщением."""
    path = CARD_FILES.get(card)
    if not path or not bot_instance.bot or not bot_instance.HAS_AIOGRAM or not path.exists():
        return
    try:
        from aiogram.types import FSInputFile
        await bot_instance.bot.send_photo(chat_id=tg_user_id, photo=FSInputFile(str(path)))
    except Exception as e:
        logger.error(f"[NotifyDispatch] Не удалось отправить карточку {card} {tg_user_id}: {e}")


async def send_with_card(tg_user_id: int, text: str, card: Optional[str] = None, reply_markup=None):
    """Шлёт текст и карточку ОДНИМ сообщением (текст — caption у фото), чтобы
    в чате не было двух разрозненных сообщений подряд. Если текст не влезает
    в лимит caption (1024 симв., напр. money-дайджест из многих строк) —
    падаем назад на картинку и текст раздельно, как было раньше."""
    if not bot_instance.bot:
        return
    path = CARD_FILES.get(card) if card else None
    if path and bot_instance.HAS_AIOGRAM and path.exists() and len(text) <= _CAPTION_LIMIT:
        try:
            from aiogram.types import FSInputFile
            await bot_instance.bot.send_photo(
                chat_id=tg_user_id, photo=FSInputFile(str(path)), caption=text,
                parse_mode="Markdown", reply_markup=reply_markup,
            )
            return
        except Exception as e:
            logger.error(f"[NotifyDispatch] Не удалось отправить карточку+текст {card} {tg_user_id}: {e}")
    if card:
        await send_card(tg_user_id, card)
    try:
        await bot_instance.bot.send_message(
            chat_id=tg_user_id, text=text, parse_mode="Markdown",
            disable_web_page_preview=True, reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"[NotifyDispatch] Не удалось отправить {tg_user_id}: {e}")


async def _send(tg_user_id: int, text: str, reply_markup=None, card: Optional[str] = None):
    await send_with_card(tg_user_id, text, card=card, reply_markup=reply_markup)


async def dispatch(
    store_id: str, group: str, text: str, *,
    urgent: bool = False, reply_markup=None, dedup_key: Optional[str] = None,
    amount: Optional[float] = None, card: Optional[str] = None,
):
    """group: 'chats' | 'problems' | 'money'. urgent=True — игнорирует тихие
    часы и батчинг (Express-заказы, «сессия протухла» — по плану §6 всегда
    приходят сразу). amount — сумма заказа для сравнения с персональным
    порогом new_order_min_sum (только для не-urgent money-уведомлений).
    card — ключ из CARD_FILES: отправляется картинкой перед текстом (не
    применяется к money-буферу — там дайджест из нескольких строк одним
    сообщением, картинка шлётся один раз при флаше, см. _flush_money_buffers)."""
    now = datetime.datetime.now()
    if dedup_key:
        key = (store_id, dedup_key)
        last = _dedup_sent.get(key)
        if last and now - last < _DEDUP_WINDOW:
            return
        _dedup_sent[key] = now

    recipients = await asyncio.to_thread(_recipients_for_store, store_id)
    for tg_user_id in recipients:
        prefs = await asyncio.to_thread(notification_prefs.get_prefs, tg_user_id, store_id)
        if not prefs.get(group, True):
            continue

        if (
            group == "money" and not urgent and amount is not None
            and prefs.get("new_order_min_sum") is not None
            and amount < prefs["new_order_min_sum"]
        ):
            continue

        if group == "money" and not urgent:
            buf_key = (store_id, tg_user_id)
            _money_buffer.setdefault(buf_key, {"lines": [], "first_at": now})["lines"].append(text)
            continue

        if not urgent and _in_quiet_hours(prefs.get("quiet_from"), prefs.get("quiet_to"), now):
            _quiet_queue.setdefault((store_id, tg_user_id), []).append(text)
            continue

        await _send(tg_user_id, text, reply_markup, card=card)


async def _flush_money_buffers():
    now = datetime.datetime.now()
    ready_keys = [k for k, v in _money_buffer.items() if now - v["first_at"] >= _MONEY_DEBOUNCE]
    for key in ready_keys:
        buf = _money_buffer.pop(key, None)
        if not buf or not buf["lines"]:
            continue
        _, tg_user_id = key
        lines = buf["lines"]
        if len(lines) == 1:
            text = lines[0]
            card = "new_order"
        else:
            text = f"💰 *{len(lines)} событий за последнюю минуту:*\n" + "\n".join(f"• {l}" for l in lines)
            card = "money_digest"
        await _send(tg_user_id, text, card=card)


async def _flush_quiet_queues():
    now = datetime.datetime.now()
    for key in list(_quiet_queue.keys()):
        store_id, tg_user_id = key
        prefs = await asyncio.to_thread(notification_prefs.get_prefs, tg_user_id, store_id)
        if _in_quiet_hours(prefs.get("quiet_from"), prefs.get("quiet_to"), now):
            continue
        lines = _quiet_queue.pop(key, None)
        if not lines:
            continue
        text = f"🌙 *Пока вы спали ({len(lines)}):*\n" + "\n".join(f"• {l}" for l in lines)
        await _send(tg_user_id, text)


def _all_store_ids() -> List[str]:
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import Store
        return [s.id for s in session.query(Store).all()]
    finally:
        session.close()


async def _flush_evening_summaries():
    now = datetime.datetime.now()
    if now.hour != EVENING_SUMMARY_HOUR:
        return
    today = now.date()

    store_ids = await asyncio.to_thread(_all_store_ids)
    for store_id in store_ids:
        if _last_evening_summary.get(store_id) == today:
            continue
        _last_evening_summary[store_id] = today
        try:
            from backend.state_helpers import _load_sales_summary
            summary = await asyncio.to_thread(_load_sales_summary, store_id, 1)
        except Exception as e:
            logger.error(f"[NotifyDispatch] Не удалось построить вечернюю сводку для {store_id}: {e}")
            continue
        orders = summary.get("total_orders") or 0
        if not orders:
            continue
        revenue = summary.get("total_revenue") or 0.0
        text = (
            f"🌆 *Итоги дня*\n"
            f"Заказов: {orders}\n"
            f"Выручка: {revenue:,.0f} ₸".replace(",", " ")
        )
        await dispatch(store_id, "money", text, urgent=True, card="money_digest")


async def flush_loop():
    """Один фоновый таск на всё приложение (стартует в worker.py вместе с
    polling'ом бота): раз в ~30с досылает money-дайджесты после 60с окна
    тишины, quiet-hours очередь как только тихие часы закончились, и раз в
    день (в EVENING_SUMMARY_HOUR) — вечернюю сводку по каждому магазину."""
    logger.info("[NotifyDispatch] Фоновый цикл рассылки уведомлений запущен.")
    while True:
        try:
            await _flush_money_buffers()
            await _flush_quiet_queues()
            await _flush_evening_summaries()
        except Exception as e:
            logger.error(f"[NotifyDispatch] Ошибка фонового цикла: {e}", exc_info=True)
        await asyncio.sleep(30)
