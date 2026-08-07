"""
Идентификация Telegram <-> магазин (см. docs/TELEGRAM_BOT_PLAN.md, Фаза 1).

Заменяет ручной ввод числового Telegram ID (старый ChatbotSettingsDB.admin_ids,
см. chatbot/config.py) на deep-link: владелец жмёт кнопку в кабинете -> получает
одноразовый токен -> t.me/<bot>?start=<token> -> бот сам связывает Telegram-
аккаунт с User и его ролью в сторе (UserStore.role — уже существующая схема,
никакой отдельной системы прав не заводим).

Все функции синхронные (тот же паттерн, что chatbot/config.py и
state_helpers.py — единая sync-сессия SQLAlchemy на вызов), т.к. вызываются
и из sync/async Telegram-хэндлеров, и из async FastAPI-роутов через
asyncio.to_thread.
"""
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

TOKEN_TTL_MINUTES = 15
EMPLOYEE_ROLES = ("MANAGER", "VIEWER")


def _now() -> datetime:
    return datetime.utcnow()


def generate_link_token(
    store_id: str,
    created_by: str,
    self_user_id: Optional[str] = None,
    invite_role: Optional[str] = None,
    max_uses: int = 1,
    ttl_minutes: int = TOKEN_TTL_MINUTES,
) -> Optional[str]:
    """
    Создаёт одноразовый (или max_uses-разовый) токен привязки.

    self_user_id задан, invite_role=None — владелец привязывает СЕБЯ.
    self_user_id=None, invite_role задан ('MANAGER'/'VIEWER') — приглашение
    нового сотрудника, бот заведёт telegram-only User сам при активации.
    """
    if invite_role is not None and invite_role not in EMPLOYEE_ROLES:
        raise ValueError(f"Недопустимая роль приглашения: {invite_role}")

    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import TelegramLinkTokenDB
        token = secrets.token_urlsafe(32)
        row = TelegramLinkTokenDB(
            token=token,
            user_id=self_user_id,
            store_id=store_id,
            invite_role=invite_role,
            created_by=created_by,
            expires_at=_now() + timedelta(minutes=ttl_minutes),
            max_uses=max_uses,
            used_count=0,
        )
        session.add(row)
        session.commit()
        return token
    except Exception as e:
        logger.error(f"Error creating telegram link token: {e}")
        session.rollback()
        return None
    finally:
        session.close()


def consume_link_token(
    token: str, tg_user_id: int, tg_username: Optional[str], tg_first_name: Optional[str],
    language_code: Optional[str] = None,
) -> dict:
    """
    Активирует токен по нажатию /start <token> в боте.

    Возвращает {"success": bool, "error": Optional[str], "store_id", "store_name",
    "role", "is_new_employee": bool}.
    """
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return {"success": False, "error": "БД недоступна"}
    try:
        from backend.db.models import TelegramLinkTokenDB, TelegramLinkDB, UserStore, Store, User

        row = session.get(TelegramLinkTokenDB, token)
        if not row:
            return {"success": False, "error": "Ссылка недействительна или уже была использована."}
        if row.expires_at < _now():
            return {"success": False, "error": "Срок действия ссылки истёк (15 минут). Запросите новую в кабинете."}
        if row.used_count >= row.max_uses:
            return {"success": False, "error": "Эта ссылка уже была использована."}

        store = session.get(Store, row.store_id)
        if not store:
            return {"success": False, "error": "Магазин не найден."}

        is_new_employee = False

        if row.user_id:
            # Владелец/существующий пользователь привязывает себя.
            target_user_id = row.user_id
        else:
            # Приглашение сотрудника — ищем, не привязан ли уже этот Telegram
            # к какому-то User (повторное использование чужой инвайт-ссылки
            # тем же человеком не должно плодить дубликаты).
            existing_link = session.get(TelegramLinkDB, tg_user_id)
            if existing_link:
                target_user_id = existing_link.user_id
            else:
                import uuid
                new_user = User(
                    id=str(uuid.uuid4()),
                    email=f"tg{tg_user_id}@telegram.local",
                    password_hash="",
                    full_name=tg_first_name or tg_username or f"TG {tg_user_id}",
                    is_active=True,
                    is_verified=True,
                    is_telegram_only=True,
                )
                session.add(new_user)
                session.flush()
                target_user_id = new_user.id
                is_new_employee = True

            us = session.get(UserStore, (target_user_id, row.store_id))
            if not us:
                session.add(UserStore(user_id=target_user_id, store_id=row.store_id, role=row.invite_role))
            else:
                us.role = row.invite_role

        link = session.get(TelegramLinkDB, tg_user_id)
        if not link:
            link = TelegramLinkDB(
                tg_user_id=tg_user_id,
                user_id=target_user_id,
                active_store_id=row.store_id,
                tg_username=tg_username,
                tg_first_name=tg_first_name,
                language_code=language_code,
                linked_at=_now(),
                last_seen_at=_now(),
            )
            session.add(link)
        else:
            link.user_id = target_user_id
            link.active_store_id = row.store_id
            link.tg_username = tg_username
            link.tg_first_name = tg_first_name
            if language_code:
                link.language_code = language_code
            link.last_seen_at = _now()

        row.used_count += 1

        us_role = session.get(UserStore, (target_user_id, row.store_id))
        role = us_role.role if us_role else (row.invite_role or "OWNER")

        session.commit()
        return {
            "success": True,
            "error": None,
            "store_id": row.store_id,
            "store_name": store.name,
            "role": role,
            "is_new_employee": is_new_employee,
        }
    except Exception as e:
        logger.error(f"Error consuming telegram link token: {e}")
        session.rollback()
        return {"success": False, "error": "Внутренняя ошибка привязки."}
    finally:
        session.close()


def get_link(tg_user_id: int) -> Optional[dict]:
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import TelegramLinkDB
        row = session.get(TelegramLinkDB, tg_user_id)
        if not row:
            return None
        return {
            "tg_user_id": row.tg_user_id,
            "user_id": row.user_id,
            "active_store_id": row.active_store_id,
            "language_code": row.language_code,
        }
    finally:
        session.close()


def touch_last_seen(
    tg_user_id: int, tg_username: Optional[str] = None, tg_first_name: Optional[str] = None,
    language_code: Optional[str] = None,
):
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import TelegramLinkDB
        row = session.get(TelegramLinkDB, tg_user_id)
        if not row:
            return
        row.last_seen_at = _now()
        if tg_username is not None:
            row.tg_username = tg_username
        if tg_first_name is not None:
            row.tg_first_name = tg_first_name
        if language_code:
            row.language_code = language_code
        session.commit()
    except Exception as e:
        logger.error(f"Error touching telegram link last_seen: {e}")
        session.rollback()
    finally:
        session.close()


def list_user_stores(user_id: str) -> list:
    """Все магазины пользователя с его ролью в каждом — для /switch и меню."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import UserStore, Store
        from sqlalchemy import select
        rows = session.execute(
            select(Store, UserStore.role).join(UserStore, Store.id == UserStore.store_id).where(UserStore.user_id == user_id)
        ).all()
        return [{"store_id": store.id, "name": store.name, "role": role} for store, role in rows]
    finally:
        session.close()


def get_active_store_id(tg_user_id: int) -> Optional[str]:
    """
    Резолвит активный магазин для этого Telegram-аккаунта. Если
    active_store_id не задан (или больше не принадлежит пользователю) —
    падает на единственный магазин пользователя, если он ровно один.
    """
    link = get_link(tg_user_id)
    if not link:
        return None

    stores = list_user_stores(link["user_id"])
    if not stores:
        return None

    if link["active_store_id"] and any(s["store_id"] == link["active_store_id"] for s in stores):
        return link["active_store_id"]

    if len(stores) == 1:
        return stores[0]["store_id"]

    return None


def set_active_store(tg_user_id: int, store_id: str) -> bool:
    link = get_link(tg_user_id)
    if not link:
        return False
    stores = list_user_stores(link["user_id"])
    if not any(s["store_id"] == store_id for s in stores):
        return False

    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import TelegramLinkDB
        row = session.get(TelegramLinkDB, tg_user_id)
        if not row:
            return False
        row.active_store_id = store_id
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error setting active store for telegram link: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def get_tg_user_id_for_user(user_id: str) -> Optional[int]:
    """Обратный резолв: по веб-User → его привязанный Telegram-аккаунт (если
    есть). Нужен кабинету, чтобы читать/писать personal notification prefs
    (keyed по tg_user_id) от лица залогиненного веб-пользователя."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import TelegramLinkDB
        from sqlalchemy import select
        row = session.execute(
            select(TelegramLinkDB).where(TelegramLinkDB.user_id == user_id)
        ).scalars().first()
        return row.tg_user_id if row else None
    finally:
        session.close()


def get_role(user_id: str, store_id: str) -> Optional[str]:
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import UserStore
        row = session.get(UserStore, (user_id, store_id))
        return row.role if row else None
    finally:
        session.close()


def is_owner(tg_user_id: int, store_id: str) -> bool:
    link = get_link(tg_user_id)
    if not link:
        return False
    return get_role(link["user_id"], store_id) == "OWNER"


def resolve_bot_access(tg_user_id: int) -> dict:
    """
    Единая точка входа для Telegram-хэндлеров: определяет, имеет ли этот
    Telegram-аккаунт доступ к боту, к какому ОДНОМУ магазину (печать/спул
    требуют конкретный store_id), и с какой ролью.

    Приоритет:
    1. Новая deep-link привязка (TelegramLinkDB) — активный магазин из
       active_store_id, либо единственный магазин пользователя.
    2. Легаси-механизм (см. HANDOFF 3.15): ChatbotSettingsDB.admin_ids по
       каждому стору (config.get_store_ids_for_admin) — если ровно один
       стор, используем его напрямую (роль условно OWNER, старый механизм
       не знал ролей). Если их несколько — берём первый детерминированно
       (тот же паттерн, что products_sync_worker для legacy multi-store,
       см. HANDOFF 3.6): в проде сегодня ровно один реальный магазин,
       полноценный /switch для этого легаси-пути не реализован (только
       для новых deep-link привязок).
    3. Совсем легаси общий список из .env (store_id=None, dev-бутстрап без
       Store в БД вообще).

    Возвращает:
      {"authorized": bool, "store_id": Optional[str], "role": Optional[str],
       "needs_store_selection": bool, "store_choices": list}
    """
    link = get_link(tg_user_id)
    if link:
        stores = list_user_stores(link["user_id"])
        if not stores:
            return {"authorized": False, "store_id": None, "role": None, "needs_store_selection": False, "store_choices": []}

        store_id = get_active_store_id(tg_user_id)
        if store_id:
            role = get_role(link["user_id"], store_id)
            return {"authorized": True, "store_id": store_id, "role": role, "needs_store_selection": False, "store_choices": stores}

        return {"authorized": True, "store_id": None, "role": None, "needs_store_selection": True, "store_choices": stores}

    from backend.chatbot import config as chatbot_config
    legacy_store_ids = chatbot_config.get_store_ids_for_admin(str(tg_user_id))
    if legacy_store_ids:
        return {"authorized": True, "store_id": legacy_store_ids[0], "role": "OWNER", "needs_store_selection": False, "store_choices": []}

    legacy_admin_ids = chatbot_config.load_admin_ids()
    if str(tg_user_id) in legacy_admin_ids:
        return {"authorized": True, "store_id": None, "role": "OWNER", "needs_store_selection": False, "store_choices": []}

    return {"authorized": False, "store_id": None, "role": None, "needs_store_selection": False, "store_choices": []}


def unlink_self(tg_user_id: int) -> bool:
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import TelegramLinkDB
        row = session.get(TelegramLinkDB, tg_user_id)
        if not row:
            return False
        session.delete(row)
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error unlinking telegram account: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def list_linked_members(store_id: str) -> list:
    """Для кабинета (owner видит, кто привязан) — список Telegram-аккаунтов,
    у чьего user_id есть UserStore на этот store_id."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import TelegramLinkDB, UserStore
        from sqlalchemy import select
        rows = session.execute(
            select(TelegramLinkDB, UserStore.role)
            .join(UserStore, TelegramLinkDB.user_id == UserStore.user_id)
            .where(UserStore.store_id == store_id)
        ).all()
        return [
            {
                "tg_user_id": link.tg_user_id,
                "tg_username": link.tg_username,
                "tg_first_name": link.tg_first_name,
                "role": role,
                "linked_at": link.linked_at.isoformat() if link.linked_at else None,
            }
            for link, role in rows
        ]
    finally:
        session.close()


def unlink_member(store_id: str, tg_user_id: int, requesting_user_id: str) -> bool:
    """Владелец отвязывает сотрудника от СВОЕГО магазина. Не трогает связь
    сотрудника с другими магазинами (мультитенантный сотрудник — редкий, но
    возможный случай), удаляет только UserStore(store_id) и, если это была
    последняя связь сотрудника с сотрудник-only аккаунтом, весь User целиком."""
    if get_role(requesting_user_id, store_id) != "OWNER":
        return False

    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import TelegramLinkDB, UserStore, User
        link = session.get(TelegramLinkDB, tg_user_id)
        if not link:
            return False
        us = session.get(UserStore, (link.user_id, store_id))
        if not us:
            return False
        session.delete(us)
        session.flush()

        user = session.get(User, link.user_id)
        if user and user.is_telegram_only:
            from sqlalchemy import select
            remaining = session.execute(select(UserStore).where(UserStore.user_id == link.user_id)).first()
            if not remaining:
                session.delete(link)
                session.delete(user)
                session.commit()
                return True

        if link.active_store_id == store_id:
            link.active_store_id = None
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error unlinking telegram member: {e}")
        session.rollback()
        return False
    finally:
        session.close()
