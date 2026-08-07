"""Аудит действий из бота, меняющих состояние магазина (Ф4.4/Ф5,
docs/TELEGRAM_BOT_PLAN.md §4.4: "Каждое действие из бота, меняющее
состояние ... пишется в аудит с пометкой «через Telegram, @username»")."""
import logging

logger = logging.getLogger(__name__)


def log_action(store_id: str, tg_user_id: int, action: str, detail: str = ""):
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import TelegramAuditLogDB
        session.add(TelegramAuditLogDB(
            store_id=store_id, tg_user_id=tg_user_id, action=action, detail=detail or None,
        ))
        session.commit()
    except Exception as e:
        logger.error(f"[Audit] Не удалось записать действие '{action}': {e}")
        session.rollback()
    finally:
        session.close()
