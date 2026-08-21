import os
import json
import logging
import datetime
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATES_DIR = os.path.join(BASE_DIR, "states")
CONFIG_FILE = os.path.join(STATES_DIR, "chatbot_config.json")
SESSION_FILE = os.path.join(STATES_DIR, "session.json")

# ── Загрузка .env ──
def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
        except Exception as e:
            logger.warning(f"Ошибка чтения .env: {e}")

load_env()

# Слайс 4: анти-спам флаг "уже уведомили о протухших куках" — per-store,
# in-memory (как _chatbot_state/_stock_locks в других модулях; не требует
# персистентности, это не был файл и раньше — просто общий bool на процесс).
_cookie_alert_sent: dict = {}

def is_cookie_alert_sent(store_id: Optional[str] = None) -> bool:
    return _cookie_alert_sent.get(store_id, False)

def set_cookie_alert_sent(store_id: Optional[str], value: bool):
    _cookie_alert_sent[store_id] = value

def any_cookie_alert_sent() -> bool:
    """Для Telegram-команды /status — бот единый процесс на все магазины
    (Phase 5 де-глобализации не было), поэтому там нет store-контекста."""
    return any(_cookie_alert_sent.values())

# Слайс 4: PROCESSED_CHATS раньше был единым словарём group_id -> msg_id/
# "HUMAN_OVERRIDE" на все магазины — состояние блокировки чата одного стора
# было видно (и снималось) из другого. Теперь персистится per-store в БД
# (ChatStateDB), см. get_chat_state/mark_chat_replied/set_chat_human_override/
# clear_chat_human_override ниже. store_id=None (легаси, dev-бутстрап без
# Store в БД) — этот словарь никогда не был файлом, поэтому фоллбек тут
# просто in-memory dict, а не чтение с диска.
_legacy_chat_state: dict = {}  # group_id -> msg_id или "HUMAN_OVERRIDE"

def get_chat_state(store_id: Optional[str], group_id: str) -> Optional[dict]:
    """Возвращает {'last_replied_msg_id': str|None, 'human_override': bool} или None."""
    if store_id is None:
        val = _legacy_chat_state.get(group_id)
        if val is None:
            return None
        if val == "HUMAN_OVERRIDE":
            return {"last_replied_msg_id": None, "human_override": True}
        return {"last_replied_msg_id": val, "human_override": False}

    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import ChatStateDB
        row = session.get(ChatStateDB, (store_id, group_id))
        if not row:
            return None
        return {
            "last_replied_msg_id": row.last_replied_msg_id,
            "human_override": row.human_override,
            "awaiting_review_reply": row.awaiting_review_reply,
            "client_name": row.client_name,
            "blocked_at": row.blocked_at,
        }
    finally:
        session.close()

def is_chat_human_override(store_id: Optional[str], group_id: str) -> bool:
    st = get_chat_state(store_id, group_id)
    return bool(st and st.get("human_override"))

def get_chat_last_replied(store_id: Optional[str], group_id: str) -> Optional[str]:
    st = get_chat_state(store_id, group_id)
    return st.get("last_replied_msg_id") if st else None

def _upsert_chat_state(store_id: Optional[str], group_id: str, **fields):
    if store_id is None:
        if "human_override" in fields and fields["human_override"]:
            _legacy_chat_state[group_id] = "HUMAN_OVERRIDE"
        elif "last_replied_msg_id" in fields:
            _legacy_chat_state[group_id] = fields["last_replied_msg_id"]
        return

    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import ChatStateDB
        row = session.get(ChatStateDB, (store_id, group_id))
        if not row:
            row = ChatStateDB(store_id=store_id, group_id=group_id, last_replied_msg_id=None, human_override=False)
            session.add(row)
        for k, v in fields.items():
            setattr(row, k, v)
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting chat state to DB: {e}")
        session.rollback()
    finally:
        session.close()

def mark_chat_replied(store_id: Optional[str], group_id: str, msg_id: str):
    _upsert_chat_state(store_id, group_id, last_replied_msg_id=msg_id)

CHAT_OVERRIDE_TTL = datetime.timedelta(days=2)

def set_chat_human_override(store_id: Optional[str], group_id: str, client_name: Optional[str] = None):
    fields = {"human_override": True, "blocked_at": datetime.datetime.now().isoformat()}
    if client_name:
        fields["client_name"] = client_name
    _upsert_chat_state(store_id, group_id, **fields)

def clear_chat_human_override(store_id: Optional[str], group_id: str) -> bool:
    """Возвращает True, если чат реально был заблокирован (и теперь разблокирован)."""
    if store_id is None:
        if _legacy_chat_state.get(group_id) == "HUMAN_OVERRIDE":
            _legacy_chat_state.pop(group_id, None)
            return True
        return False

    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import ChatStateDB
        row = session.get(ChatStateDB, (store_id, group_id))
        if not row or not row.human_override:
            return False
        row.human_override = False
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error clearing chat human override: {e}")
        session.rollback()
        return False
    finally:
        session.close()

def clear_human_override_any_store(group_id: str) -> Optional[str]:
    """Для Telegram unblock-колбэка — callback_data не несёт store_id (бот
    один процесс на все магазины, см. HANDOFF), поэтому ищет чат по group_id
    среди ВСЕХ магазинов + легаси in-memory dict. Возвращает store_id (или
    "legacy" для in-memory фоллбека), где чат был разблокирован, или None,
    если нигде не был заблокирован."""
    if _legacy_chat_state.get(group_id) == "HUMAN_OVERRIDE":
        _legacy_chat_state.pop(group_id, None)
        return "legacy"

    from backend.state_helpers import _get_sync_db_session
    from sqlalchemy import select
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import ChatStateDB
        row = session.scalars(
            select(ChatStateDB).where(ChatStateDB.group_id == group_id, ChatStateDB.human_override == True)
        ).first()
        if not row:
            return None
        row.human_override = False
        session.commit()
        return row.store_id
    except Exception as e:
        logger.error(f"Error clearing chat human override (any store): {e}")
        session.rollback()
        return None
    finally:
        session.close()

_legacy_awaiting_review_reply: set = set()

def is_awaiting_review_reply(store_id: Optional[str], group_id: str) -> bool:
    if store_id is None:
        return group_id in _legacy_awaiting_review_reply
    st = get_chat_state(store_id, group_id)
    return bool(st and st.get("awaiting_review_reply"))

def set_awaiting_review_reply(store_id: Optional[str], group_id: str):
    if store_id is None:
        _legacy_awaiting_review_reply.add(group_id)
        return
    _upsert_chat_state(store_id, group_id, awaiting_review_reply=True)

def clear_awaiting_review_reply(store_id: Optional[str], group_id: str) -> bool:
    """Возвращает True, если флаг реально был установлен (и теперь снят)."""
    if store_id is None:
        if group_id in _legacy_awaiting_review_reply:
            _legacy_awaiting_review_reply.discard(group_id)
            return True
        return False
    if not is_awaiting_review_reply(store_id, group_id):
        return False
    _upsert_chat_state(store_id, group_id, awaiting_review_reply=False)
    return True

def count_blocked_chats(store_id: Optional[str] = None) -> int:
    return len(list_blocked_chat_ids(store_id))

def list_blocked_chat_ids(store_id: Optional[str] = None) -> list:
    return [c["group_id"] for c in list_blocked_chats_detailed(store_id)]

def list_blocked_chats_detailed(store_id: Optional[str] = None) -> list:
    """Как list_blocked_chat_ids, но с именем клиента и временем блокировки —
    для читаемого списка в /chats. Заодно снимает блокировки, которые висят
    дольше CHAT_OVERRIDE_TTL (менеджер забыл вернуть чат ИИ): такой чат сам
    возвращается под управление ИИ, а не копится в списке вечно."""
    from backend.state_helpers import _get_sync_db_session
    from sqlalchemy import select
    session = _get_sync_db_session()
    out = []
    if session:
        try:
            from backend.db.models import ChatStateDB
            stmt = select(ChatStateDB).where(ChatStateDB.human_override == True)
            if store_id is not None:
                stmt = stmt.where(ChatStateDB.store_id == store_id)
            now = datetime.datetime.now()
            expired = []
            for r in session.scalars(stmt).all():
                blocked_at = None
                if r.blocked_at:
                    try:
                        blocked_at = datetime.datetime.fromisoformat(r.blocked_at)
                    except ValueError:
                        blocked_at = None
                if blocked_at and now - blocked_at > CHAT_OVERRIDE_TTL:
                    expired.append(r)
                    continue
                out.append({
                    "group_id": r.group_id,
                    "store_id": r.store_id,
                    "client_name": r.client_name,
                    "blocked_at": r.blocked_at,
                })
            for r in expired:
                r.human_override = False
            if expired:
                session.commit()
        finally:
            session.close()
    if store_id is None:
        out += [
            {"group_id": gid, "store_id": None, "client_name": None, "blocked_at": None}
            for gid, val in _legacy_chat_state.items() if val == "HUMAN_OVERRIDE"
        ]
    return out

def count_processed_chats(store_id: Optional[str] = None) -> int:
    from backend.state_helpers import _get_sync_db_session
    from sqlalchemy import select
    session = _get_sync_db_session()
    count = 0
    if session:
        try:
            from backend.db.models import ChatStateDB
            stmt = select(ChatStateDB)
            if store_id is not None:
                stmt = stmt.where(ChatStateDB.store_id == store_id)
            count = len(session.scalars(stmt).all())
        finally:
            session.close()
    if store_id is None:
        count += len(_legacy_chat_state)
    return count

LOGISTICS_TRIGGERS = {
    "ru": {
        "keywords": ["доставка", "когда приедет", "где заказ", "задерживается", "доставили", "курьер", "почему долго"],
        "reply": "Мы бы с радостью подсказали, но всю логистику и доставку полностью организует сам Kaspi. Мы собираем и передаем заказы вовремя, а дальше за движение посылки отвечает курьерская служба платформы. Пожалуйста, напишите в службу поддержки 'Kaspi Гид' прямо в приложении — они видят точное местоположение курьера и быстро помогут!"
    },
    "kk": {
        "keywords": ["жеткізу", "қашан келеді", "тапсырыс қайда", "кешігіп", "курьер", "неге ұзақ"],
        "reply": "Біз тапсырысты уақытында жинап, курьерге өткізіп жібердік. Алайда, жеткізу жұмыстарымен толықтай Kaspi-дің өзі айналысады. Тапсырыстың қазір қай жерде екенін білу үшін қосымшадағы 'Kaspi Гид' қолдау қызметіне жазсаңыз, олар курьермен байланысып, сізге тез арада көмектеседі!"
    }
}

def load_chatbot_config(store_id: Optional[str] = None) -> dict:
    """
    Загружает конфигурацию чат-бота из .env (деплойные креды — общие на всё
    приложение, один Telegram-бот, см. HANDOFF) + enabled.

    store_id given (Слайс 4): enabled из ChatbotSettingsDB этого магазина.
    store_id=None (легаси): старый общий CONFIG_FILE.
    """
    load_env()

    deepseek_api = os.environ.get("DEEPSEEK_API", os.environ.get("DEEPSEEK_API_KEY", "")).strip()
    tg_bot_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    tg_bot_proxy = os.environ.get("TG_BOT_PROXY", os.environ.get("TG_PROXY", "")).strip()
    # НЕ дефолтим на "20186986" (реальный numeric ID Barkhan) — легаси
    # однотенантный конфиг без явно заданного MERCHANT_ID должен явно
    # остаться пустым, а не тихо выдавать себя за Barkhan.
    merchant_id = os.environ.get("MERCHANT_ID", "").strip()

    config_data = {
        "enabled": True,
        "deepseek_api": deepseek_api,
        "deepseek_api_key": deepseek_api,
        "tg_bot_token": tg_bot_token,
        "tg_bot_proxy": tg_bot_proxy,
        "tg_proxy": tg_bot_proxy,
        "use_tg_proxy": bool(tg_bot_proxy),
        "merchant_id": merchant_id,
    }

    if store_id is not None:
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        if session:
            try:
                from backend.db.models import ChatbotSettingsDB
                row = session.get(ChatbotSettingsDB, store_id)
                if row:
                    config_data["enabled"] = row.enabled
            finally:
                session.close()
        return config_data

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if "enabled" in data:
                        config_data["enabled"] = data["enabled"]
        except Exception as e:
            logger.error(f"Ошибка чтения {CONFIG_FILE}: {e}")

    return config_data

def save_chatbot_config(new_config: dict, store_id: Optional[str] = None) -> dict:
    """Сохраняет пользовательские настройки (enabled) этого магазина."""
    current = load_chatbot_config(store_id=store_id)

    to_save = {}
    if "enabled" in new_config:
        to_save["enabled"] = new_config["enabled"]

    if store_id is not None:
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        if session:
            try:
                from backend.db.models import ChatbotSettingsDB
                enabled_val = to_save.get("enabled", current.get("enabled", True))
                row = session.get(ChatbotSettingsDB, store_id)
                if not row:
                    row = ChatbotSettingsDB(store_id=store_id, enabled=enabled_val)
                    session.add(row)
                else:
                    row.enabled = enabled_val
                session.commit()
            except Exception as e:
                logger.error(f"Error persisting chatbot settings to DB: {e}")
                session.rollback()
            finally:
                session.close()
        current.update(to_save)
        return current

    os.makedirs(STATES_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)

    logger.info("⚙️ Конфигурация чат-бота успешно обновлена.")
    current.update(to_save)
    return current

def get_store_name(store_id: str) -> str:
    """Человекочитаемое имя магазина для текстов Telegram-алертов/команд —
    merchant_id ничего не говорит владельцу, если у него несколько сторов.
    store_id=None (легаси .env-админ без Store в БД) и Store.name=NULL —
    оба раньше рендерились в текст как буквальное "None"."""
    fallback = store_id or "Магазин"
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return fallback
    try:
        from backend.db.models import Store
        store = session.get(Store, store_id) if store_id else None
        return (store.name if store else None) or fallback
    finally:
        session.close()

PRINT_SETTINGS_FILE = os.path.join(STATES_DIR, "print_settings.json")

DEFAULT_PRINT_SETTINGS = {
    "output_layout": "auto",
    "express_mode": "separate",
    "sort_preset": "filename",
    "deduplicate_history": True,
    "auto_rotate": True,
    # Учёт уже распечатанных накладных: always_all (печатать всё, без учёта
    # истории) | always_new (тихо пропускать дубликаты — прежнее поведение
    # по умолчанию) | always_ask (спрашивать при каждой печати).
    "dup_handling_mode": "always_new"
}

def load_print_settings(store_id: Optional[str] = None) -> dict:
    """
    store_id given: настройки печати этого магазина (StorePrintSettingsDB).
    store_id=None (легаси, dev-бутстрап без Store в БД): старый общий файл.
    """
    if store_id is not None:
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        merged = DEFAULT_PRINT_SETTINGS.copy()
        if session:
            try:
                from backend.db.models import StorePrintSettingsDB
                row = session.get(StorePrintSettingsDB, store_id)
                if row:
                    try:
                        merged.update(json.loads(row.payload))
                    except Exception:
                        pass
            finally:
                session.close()
        return merged

    if os.path.exists(PRINT_SETTINGS_FILE):
        try:
            with open(PRINT_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                merged = DEFAULT_PRINT_SETTINGS.copy()
                merged.update(data)
                return merged
        except Exception:
            pass
    return DEFAULT_PRINT_SETTINGS.copy()

def save_print_settings(settings: dict, store_id: Optional[str] = None) -> dict:
    current = load_print_settings(store_id=store_id)
    current.update(settings)

    if store_id is not None:
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        if session:
            try:
                from backend.db.models import StorePrintSettingsDB
                payload = json.dumps(current, ensure_ascii=False)
                row = session.get(StorePrintSettingsDB, store_id)
                if not row:
                    session.add(StorePrintSettingsDB(store_id=store_id, payload=payload))
                else:
                    row.payload = payload
                session.commit()
            except Exception as e:
                logger.error(f"Error persisting print settings to DB for store {store_id}: {e}")
                session.rollback()
            finally:
                session.close()
        return current

    os.makedirs(STATES_DIR, exist_ok=True)
    with open(PRINT_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    return current

