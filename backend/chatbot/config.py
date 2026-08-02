import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATES_DIR = os.path.join(BASE_DIR, "states")
CONFIG_FILE = os.path.join(STATES_DIR, "chatbot_config.json")
SESSION_FILE = os.path.join(STATES_DIR, "session.json")
ADMIN_IDS_FILE = os.path.join(STATES_DIR, "admin_ids.txt")

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
        return {"last_replied_msg_id": row.last_replied_msg_id, "human_override": row.human_override}
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

def set_chat_human_override(store_id: Optional[str], group_id: str):
    _upsert_chat_state(store_id, group_id, human_override=True)

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

def count_blocked_chats(store_id: Optional[str] = None) -> int:
    return len(list_blocked_chat_ids(store_id))

def list_blocked_chat_ids(store_id: Optional[str] = None) -> list:
    from backend.state_helpers import _get_sync_db_session
    from sqlalchemy import select
    session = _get_sync_db_session()
    ids = []
    if session:
        try:
            from backend.db.models import ChatStateDB
            stmt = select(ChatStateDB).where(ChatStateDB.human_override == True)
            if store_id is not None:
                stmt = stmt.where(ChatStateDB.store_id == store_id)
            ids = [r.group_id for r in session.scalars(stmt).all()]
        finally:
            session.close()
    if store_id is None:
        ids += [gid for gid, val in _legacy_chat_state.items() if val == "HUMAN_OVERRIDE"]
    return ids

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
    приложение, один Telegram-бот, см. HANDOFF) + enabled/admin_ids.

    store_id given (Слайс 4): enabled/admin_ids из ChatbotSettingsDB этого
    магазина. store_id=None (легаси): старый общий CONFIG_FILE.
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
        "admin_ids": ["7119137844", "8661441552"]
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
                    try:
                        parsed = json.loads(row.admin_ids)
                        if parsed:
                            config_data["admin_ids"] = parsed
                    except Exception:
                        pass
            finally:
                session.close()
        return config_data

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if data.get("admin_ids"):
                        config_data["admin_ids"] = data["admin_ids"]
                    if "enabled" in data:
                        config_data["enabled"] = data["enabled"]
        except Exception as e:
            logger.error(f"Ошибка чтения {CONFIG_FILE}: {e}")

    return config_data

def save_chatbot_config(new_config: dict, store_id: Optional[str] = None) -> dict:
    """Сохраняет пользовательские настройки (admin_ids, enabled) этого магазина."""
    current = load_chatbot_config(store_id=store_id)

    to_save = {}
    if "admin_ids" in new_config:
        to_save["admin_ids"] = new_config["admin_ids"]
    if "enabled" in new_config:
        to_save["enabled"] = new_config["enabled"]

    if store_id is not None:
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        if session:
            try:
                from backend.db.models import ChatbotSettingsDB
                admin_ids_json = json.dumps(to_save.get("admin_ids", current.get("admin_ids", [])), ensure_ascii=False)
                enabled_val = to_save.get("enabled", current.get("enabled", True))
                row = session.get(ChatbotSettingsDB, store_id)
                if not row:
                    row = ChatbotSettingsDB(store_id=store_id, enabled=enabled_val, admin_ids=admin_ids_json)
                    session.add(row)
                else:
                    row.enabled = enabled_val
                    row.admin_ids = admin_ids_json
                session.commit()
            except Exception as e:
                logger.error(f"Error persisting chatbot settings to DB: {e}")
                session.rollback()
            finally:
                session.close()
        current.update(to_save)
        return current

    os.makedirs(STATES_DIR, exist_ok=True)
    if "admin_ids" in new_config:
        try:
            with open(ADMIN_IDS_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(new_config["admin_ids"]) + "\n")
        except Exception as e:
            logger.error(f"Ошибка записи {ADMIN_IDS_FILE}: {e}")

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)

    logger.info("⚙️ Конфигурация чат-бота успешно обновлена.")
    current.update(to_save)
    return current

def load_admin_ids(store_id: Optional[str] = None) -> list:
    """Загружает ID получателей уведомлений Telegram этого магазина."""
    if store_id is not None:
        cfg = load_chatbot_config(store_id=store_id)
        return cfg.get("admin_ids") or ["7119137844", "8661441552"]

    if os.path.exists(ADMIN_IDS_FILE):
        try:
            with open(ADMIN_IDS_FILE, "r", encoding="utf-8") as f:
                ids = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
                if ids:
                    return ids
        except Exception as e:
            logger.error(f"Ошибка чтения {ADMIN_IDS_FILE}: {e}")

    cfg = load_chatbot_config()
    if cfg.get("admin_ids"):
        return cfg["admin_ids"]

    return ["7119137844", "8661441552"]

def get_store_ids_for_admin(admin_id: str) -> list:
    """
    Реверс-résolve: в каких магазинах этот Telegram ID числится получателем
    чат-уведомлений (ChatbotSettingsDB.admin_ids). Бот единый на всё
    приложение (см. HANDOFF 3.14 — рефакторинг на полностью per-store боты
    отложен пользователем на потом), но админ может отвечать за несколько
    магазинов сразу — /start и /status используют это, чтобы показать ЕГО
    магазины, а не общую по всем сводку.
    """
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import ChatbotSettingsDB
        rows = session.query(ChatbotSettingsDB).all()
        matching = []
        for row in rows:
            try:
                ids = json.loads(row.admin_ids or "[]")
            except Exception:
                ids = []
            if str(admin_id) in [str(i) for i in ids]:
                matching.append(row.store_id)
        return matching
    finally:
        session.close()

def get_store_name(store_id: str) -> str:
    """Человекочитаемое имя магазина для текстов Telegram-алертов/команд —
    merchant_id ничего не говорит владельцу, если у него несколько сторов."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return store_id
    try:
        from backend.db.models import Store
        store = session.get(Store, store_id)
        return store.name if store else store_id
    finally:
        session.close()

PRINT_SETTINGS_FILE = os.path.join(STATES_DIR, "print_settings.json")

DEFAULT_PRINT_SETTINGS = {
    "output_layout": "auto",
    "express_mode": "separate",
    "sort_preset": "filename",
    "deduplicate_history": True,
    "auto_rotate": True
}

def load_print_settings() -> dict:
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

def save_print_settings(settings: dict) -> dict:
    os.makedirs(STATES_DIR, exist_ok=True)
    current = load_print_settings()
    current.update(settings)
    with open(PRINT_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    return current

