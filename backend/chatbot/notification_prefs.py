"""Персональные настройки уведомлений (docs/TELEGRAM_BOT_PLAN.md §4.1, §6)
— per (tg_user_id, store_id). Читаются notify_dispatch.py при каждой
отправке; дефолты совпадают с планом (chats/problems вкл, money выкл)."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PREFS = {
    "chats": True,
    "problems": True,
    "money": False,
    "new_order_min_sum": None,
    "quiet_from": None,
    "quiet_to": None,
}


def get_prefs(tg_user_id: int, store_id: str) -> dict:
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return dict(DEFAULT_PREFS)
    try:
        from backend.db.models import TelegramNotificationPrefDB
        row = session.get(TelegramNotificationPrefDB, (tg_user_id, store_id))
        if not row:
            return dict(DEFAULT_PREFS)
        return {
            "chats": row.chats,
            "problems": row.problems,
            "money": row.money,
            "new_order_min_sum": row.new_order_min_sum,
            "quiet_from": row.quiet_from,
            "quiet_to": row.quiet_to,
        }
    finally:
        session.close()


def _get_or_create_row(session, tg_user_id: int, store_id: str):
    from backend.db.models import TelegramNotificationPrefDB
    row = session.get(TelegramNotificationPrefDB, (tg_user_id, store_id))
    if not row:
        row = TelegramNotificationPrefDB(tg_user_id=tg_user_id, store_id=store_id)
        session.add(row)
    return row


def toggle_group(tg_user_id: int, store_id: str, group: str) -> Optional[bool]:
    """group: 'chats' | 'problems' | 'money'."""
    if group not in ("chats", "problems", "money"):
        return None
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        row = _get_or_create_row(session, tg_user_id, store_id)
        setattr(row, group, not getattr(row, group))
        session.commit()
        return getattr(row, group)
    finally:
        session.close()


def update_prefs(tg_user_id: int, store_id: str, updates: dict) -> dict:
    """Точечное обновление любого подмножества полей за один запрос — нужен
    кабинету (веб-панель шлёт только изменённые поля, PATCH-семантика через
    exclude_unset на уровне роутера)."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return dict(DEFAULT_PREFS)
    try:
        row = _get_or_create_row(session, tg_user_id, store_id)
        for key in ("chats", "problems", "money", "new_order_min_sum", "quiet_from", "quiet_to"):
            if key in updates:
                setattr(row, key, updates[key])
        session.commit()
        return {
            "chats": row.chats,
            "problems": row.problems,
            "money": row.money,
            "new_order_min_sum": row.new_order_min_sum,
            "quiet_from": row.quiet_from,
            "quiet_to": row.quiet_to,
        }
    finally:
        session.close()


def set_quiet_hours(tg_user_id: int, store_id: str, quiet_from: Optional[str], quiet_to: Optional[str]):
    """quiet_from/quiet_to: 'HH:MM' строки или None, чтобы выключить тихие часы."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return
    try:
        row = _get_or_create_row(session, tg_user_id, store_id)
        row.quiet_from = quiet_from
        row.quiet_to = quiet_to
        session.commit()
    finally:
        session.close()
