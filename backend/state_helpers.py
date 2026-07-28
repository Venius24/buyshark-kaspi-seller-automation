import os
import json
import logging
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SESSION_FILE = "states/session.json"
OVERRIDES_FILE = "states/products_overrides.json"
DUMPING_CONFIGS_FILE = "states/dumping_configs.json"
MASTER_STOCK_GROUPS_FILE = "states/master_stock_groups.json"
PROCESSED_ORDERS_FILE = "states/processed_orders.json"
API_KEYS_FILE = "states/api_keys.json"
WEBHOOK_CONFIG_FILE = "states/webhook_config.json"
PROCESSED_PACKAGING_ORDERS_FILE = "states/processed_packaging_orders.json"

def _get_sync_db_session():
    """Возвращает синхронную сессию SQLAlchemy для безопасного сохранения состояния в БД."""
    try:
        from backend.db.database import ASYNC_DB_URL, Base
        sync_url = ASYNC_DB_URL.replace("+asyncpg", "").replace("+aiosqlite", "")
        if "sqlite" in sync_url:
            connect_args = {"check_same_thread": False}
            sync_engine = create_engine(sync_url, connect_args=connect_args)
        else:
            sync_engine = create_engine(sync_url, pool_pre_ping=True)
        
        Base.metadata.create_all(sync_engine)
        return Session(sync_engine)
    except Exception as e:
        logger.debug(f"Sync DB connection exception: {e}")
        return None

def _load_session() -> dict:
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_session(data: dict):
    os.makedirs("states", exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _get_token() -> str:
    """Retrieve saved token or return None."""
    session = _load_session()
    return session.get("token")

def _require_token() -> str:
    """Retrieve saved token or raise 401."""
    token = _get_token()
    if not token:
        raise HTTPException(status_code=401, detail="Токен не найден. Пожалуйста, авторизуйтесь.")
    return token

def extract_brand(name: Optional[str]) -> str:
    if not name:
        return "Barkhan"
    name_upper = name.upper()
    for b in ["BARKHAN", "RIDERACE", "CAMPGEAR", "VISION", "FIKS"]:
        if b in name_upper:
            return b.capitalize()
    return "Barkhan"

def _load_overrides(store_id: Optional[str] = None) -> dict:
    """
    store_id=None (legacy, pre-Phase-4d callers): unscoped load, exact
    current behavior — including the JSON-file fallback below.

    store_id given (Phase 4d-migrated callers): DB rows are filtered to that
    store. An empty result means "no overrides yet for this store", not a
    cue to fall back to the shared legacy JSON file (which mixes every
    store's overrides together).
    """
    # 1. Загрузка из базы данных
    session = _get_sync_db_session()
    overrides = {}
    if session:
        try:
            from backend.db.models import ProductOverrideDB
            stmt = select(ProductOverrideDB)
            if store_id is not None:
                stmt = stmt.where(ProductOverrideDB.store_id == store_id)
            rows = session.scalars(stmt).all()
            if rows:
                for r in rows:
                    overrides[r.sku] = {
                        "price_num": r.price_num,
                        "stock": r.stock,
                        "brand": r.brand,
                        "status": r.status
                    }
                session.close()
                return overrides
            session.close()
        except Exception as e:
            logger.debug(f"DB load overrides error: {e}")
            if session:
                session.close()

    if store_id is not None:
        return {}

    # 2. Фоллбек из локального JSON файла (только для legacy-вызовов)
    if os.path.exists(OVERRIDES_FILE):
        try:
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_override(
    sku: str,
    price: int,
    stock: int,
    brand: Optional[str] = None,
    status: Optional[str] = None,
    store_id: Optional[str] = None,
):
    overrides = _load_overrides(store_id=store_id)
    ov_data = {
        "price_num": price,
        "stock": stock,
        "brand": brand,
        "status": status or ("Активен" if stock > 0 else "Архив")
    }
    overrides[sku] = ov_data

    # Зеркалирование на диск
    os.makedirs("states", exist_ok=True)
    try:
        with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error writing overrides file: {e}")

    # Сохранение в БД
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import ProductOverrideDB
        db_obj = session.get(ProductOverrideDB, sku)
        if not db_obj:
            db_obj = ProductOverrideDB(
                sku=sku,
                store_id=store_id,
                price_num=price,
                stock=stock,
                brand=brand,
                status=ov_data["status"]
            )
            session.add(db_obj)
        else:
            if store_id is not None:
                db_obj.store_id = store_id
            db_obj.price_num = price
            db_obj.stock = stock
            if brand:
                db_obj.brand = brand
            if status:
                db_obj.status = ov_data["status"]
        session.commit()
        session.close()
    except Exception as e:
        logger.error(f"Error persisting override to DB: {e}")
        if session:
            session.rollback()
            session.close()

def _apply_overrides(products: list, store_id: Optional[str] = None) -> list:
    overrides = _load_overrides(store_id=store_id)
    for p in products:
        sku = p.get("sku")
        if "price_num" not in p:
            try:
                price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                p["price_num"] = int(price_str)
            except:
                p["price_num"] = 0
        if "stock" not in p:
            p["stock"] = 10
        if "brand" not in p:
            p["brand"] = extract_brand(p.get("name"))
            
        if sku in overrides:
            ov = overrides[sku]
            if "price_num" in ov:
                p["price_num"] = ov["price_num"]
                p["price"] = f"{ov['price_num']} ₸"
            if "stock" in ov:
                p["stock"] = ov["stock"]
            if "brand" in ov:
                p["brand"] = ov["brand"]
            if "status" in ov:
                p["status"] = ov["status"]
    return products

def _load_dumping_configs(store_id: Optional[str] = None) -> dict:
    """
    store_id=None (legacy, pre-Phase-4d callers): unscoped load, exact
    current behavior — including the JSON-file fallback below.

    store_id given (Phase 4d-migrated callers): DB rows are filtered to that
    store. An empty result is treated as "this store has no configs yet",
    NOT as a signal to fall back to the shared legacy JSON file — that file
    mixes every store's data together, so using it as a fallback for a
    scoped read would leak other stores' configs into this one's response.
    """
    # 1. Загрузка из базы данных
    session = _get_sync_db_session()
    configs = {}
    if session:
        try:
            from backend.db.models import DumpingConfigDB
            stmt = select(DumpingConfigDB)
            if store_id is not None:
                stmt = stmt.where(DumpingConfigDB.store_id == store_id)
            rows = session.scalars(stmt).all()
            if rows:
                for r in rows:
                    blacklisted = []
                    if r.blacklisted_merchants:
                        try:
                            blacklisted = json.loads(r.blacklisted_merchants)
                        except Exception:
                            blacklisted = []
                    configs[r.sku] = {
                        "sku": r.sku,
                        "min_price": float(r.min_price or 0.0),
                        "max_price": float(r.max_price or 0.0),
                        "dump_step": float(r.dump_step or 10.0),
                        "enabled": bool(r.enabled),
                        "mode": r.mode or "TOP_1",
                        "target_position": int(r.target_position or 1),
                        "max_delivery_days": r.max_delivery_days,
                        "min_seller_rating": r.min_seller_rating,
                        "same_city_only": bool(r.same_city_only),
                        "blacklisted_merchants": blacklisted,
                        "city_code": r.city_code or "750000000"
                    }
                session.close()
                return configs
            session.close()
        except Exception as e:
            logger.debug(f"DB load dumping configs error: {e}")
            if session:
                session.close()

    if store_id is not None:
        # Scoped callers never fall back to the shared legacy file — it mixes
        # every store's data, so treat "no DB rows" / "no DB session" as
        # "this store has no configs yet", not as a cue to read someone else's.
        return {}

    # 2. Фоллбек из файла с авто-миграцией в БД (только для legacy-вызовов)
    if os.path.exists(DUMPING_CONFIGS_FILE):
        try:
            with open(DUMPING_CONFIGS_FILE, "r", encoding="utf-8") as f:
                configs = json.load(f)
                if configs and session:
                    _save_dumping_configs(configs)
                return configs
        except Exception:
            pass
    return {}

def _save_dumping_configs(configs: dict, store_id: Optional[str] = None):
    """
    store_id given (Phase 4d-migrated callers): tags every created/updated
    row with store_id, so subsequent scoped loads pick it up correctly even
    if the row previously existed unscoped (store_id=None, legacy data).
    """
    # Зеркалирование в локальный JSON
    os.makedirs("states", exist_ok=True)
    try:
        with open(DUMPING_CONFIGS_FILE, "w", encoding="utf-8") as f:
            json.dump(configs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error writing dumping configs file: {e}")

    # Персистентное сохранение в БД
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import DumpingConfigDB
        for sku, cfg in configs.items():
            db_obj = session.get(DumpingConfigDB, sku)
            blacklisted_str = json.dumps(cfg.get("blacklisted_merchants") or [], ensure_ascii=False)
            if not db_obj:
                db_obj = DumpingConfigDB(
                    sku=sku,
                    store_id=store_id,
                    min_price=float(cfg.get("min_price", 0.0)),
                    max_price=float(cfg.get("max_price", 0.0)),
                    dump_step=float(cfg.get("dump_step", 10.0)),
                    enabled=bool(cfg.get("enabled", True)),
                    mode=str(cfg.get("mode", "TOP_1")),
                    target_position=int(cfg.get("target_position", 1)),
                    max_delivery_days=cfg.get("max_delivery_days"),
                    min_seller_rating=cfg.get("min_seller_rating"),
                    same_city_only=bool(cfg.get("same_city_only", False)),
                    blacklisted_merchants=blacklisted_str,
                    city_code=str(cfg.get("city_code", "750000000"))
                )
                session.add(db_obj)
            else:
                if store_id is not None:
                    db_obj.store_id = store_id
                db_obj.min_price = float(cfg.get("min_price", 0.0))
                db_obj.max_price = float(cfg.get("max_price", 0.0))
                db_obj.dump_step = float(cfg.get("dump_step", 10.0))
                db_obj.enabled = bool(cfg.get("enabled", True))
                db_obj.mode = str(cfg.get("mode", "TOP_1"))
                db_obj.target_position = int(cfg.get("target_position", 1))
                db_obj.max_delivery_days = cfg.get("max_delivery_days")
                db_obj.min_seller_rating = cfg.get("min_seller_rating")
                db_obj.same_city_only = bool(cfg.get("same_city_only", False))
                db_obj.blacklisted_merchants = blacklisted_str
                db_obj.city_code = str(cfg.get("city_code", "750000000"))
        session.commit()
        session.close()
    except Exception as e:
        logger.error(f"Error persisting dumping configs to DB: {e}")
        if session:
            session.rollback()
            session.close()

def _load_master_stock_groups() -> dict:
    # 1. Загрузка из базы данных
    session = _get_sync_db_session()
    groups = {}
    if session:
        try:
            from backend.db.models import MasterStockGroupDB
            rows = session.scalars(select(MasterStockGroupDB)).all()
            if rows:
                for r in rows:
                    linked_skus = json.loads(r.linked_skus) if r.linked_skus else []
                    dumping_cfg = json.loads(r.dumping_config) if r.dumping_config else None
                    sync_logs = json.loads(r.sync_logs) if r.sync_logs else []
                    groups[r.group_id] = {
                        "group_id": r.group_id,
                        "group_name": r.group_name,
                        "shared_stock": r.shared_stock,
                        "linked_skus": linked_skus,
                        "external_code": r.external_code,
                        "is_active": r.is_active,
                        "dumping_config": dumping_cfg,
                        "status": r.status,
                        "created_at": r.created_at,
                        "updated_at": r.updated_at,
                        "last_synced_at": r.last_synced_at,
                        "last_sync_log": r.last_sync_log,
                        "sync_logs": sync_logs
                    }
                session.close()
                return groups
            session.close()
        except Exception as e:
            logger.debug(f"DB load master stock groups error: {e}")
            if session:
                session.close()

    # 2. Фоллбек из файла с авто-миграцией в БД
    if os.path.exists(MASTER_STOCK_GROUPS_FILE):
        try:
            with open(MASTER_STOCK_GROUPS_FILE, "r", encoding="utf-8") as f:
                groups = json.load(f)
                if groups and session:
                    _save_master_stock_groups(groups)
                return groups
        except Exception:
            pass
    return {}

def _save_master_stock_groups(groups: dict):
    # Зеркалирование в локальный JSON
    os.makedirs("states", exist_ok=True)
    try:
        with open(MASTER_STOCK_GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(groups, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error writing master stock groups file: {e}")

    # Персистентное сохранение в БД
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import MasterStockGroupDB
        for gid, g in groups.items():
            db_obj = session.get(MasterStockGroupDB, gid)
            linked_str = json.dumps(g.get("linked_skus") or [], ensure_ascii=False)
            dump_cfg_str = json.dumps(g.get("dumping_config"), ensure_ascii=False) if g.get("dumping_config") else None
            logs_str = json.dumps(g.get("sync_logs") or [], ensure_ascii=False)
            if not db_obj:
                db_obj = MasterStockGroupDB(
                    group_id=gid,
                    group_name=g.get("group_name", "Группа"),
                    shared_stock=int(g.get("shared_stock", 0)),
                    linked_skus=linked_str,
                    external_code=g.get("external_code"),
                    is_active=bool(g.get("is_active", True)),
                    dumping_config=dump_cfg_str,
                    status=g.get("status", "synced"),
                    created_at=g.get("created_at"),
                    updated_at=g.get("updated_at"),
                    last_synced_at=g.get("last_synced_at"),
                    last_sync_log=g.get("last_sync_log"),
                    sync_logs=logs_str
                )
                session.add(db_obj)
            else:
                db_obj.group_name = g.get("group_name", "Группа")
                db_obj.shared_stock = int(g.get("shared_stock", 0))
                db_obj.linked_skus = linked_str
                db_obj.external_code = g.get("external_code")
                db_obj.is_active = bool(g.get("is_active", True))
                db_obj.dumping_config = dump_cfg_str
                db_obj.status = g.get("status", "synced")
                db_obj.created_at = g.get("created_at")
                db_obj.updated_at = g.get("updated_at")
                db_obj.last_synced_at = g.get("last_synced_at")
                db_obj.last_sync_log = g.get("last_sync_log")
                db_obj.sync_logs = logs_str
        session.commit()
        session.close()
    except Exception as e:
        logger.error(f"Error persisting master stock groups to DB: {e}")
        if session:
            session.rollback()
            session.close()

def _load_processed_orders() -> dict:
    if os.path.exists(PROCESSED_ORDERS_FILE):
        try:
            with open(PROCESSED_ORDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_processed_orders(orders: dict):
    os.makedirs("states", exist_ok=True)
    with open(PROCESSED_ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)

def _load_mc_cookies() -> dict:
    """
    Унифицированная загрузка кук mc-session и mc-sid из государств:
    проверяются root 'state.json', 'states/session.json', 'states/state.json'.
    """
    candidate_paths = ["state.json", "states/session.json", "states/state.json"]
    for path in candidate_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            cookies = {}
            if isinstance(data, dict):
                cookies_list = data.get("cookies", [])
                if isinstance(cookies_list, list):
                    for c in cookies_list:
                        if isinstance(c, dict) and c.get("name") in ["mc-session", "mc-sid"]:
                            cookies[c["name"]] = c["value"]
                # Также если куки за сохранены в виде прямого словаря
                if "mc-session" in data:
                    cookies["mc-session"] = data["mc-session"]
                if "mc-sid" in data:
                    cookies["mc-sid"] = data["mc-sid"]

            if "mc-session" in cookies and "mc-sid" in cookies:
                return cookies
        except Exception:
            pass
    return {}

API_KEYS_FILE = "states/api_keys.json"
WEBHOOK_CONFIG_FILE = "states/webhook_config.json"

def _load_api_keys() -> dict:
    if os.path.exists(API_KEYS_FILE):
        try:
            with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_api_keys(keys: dict):
    os.makedirs("states", exist_ok=True)
    with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, ensure_ascii=False, indent=2)

def _load_webhook_config() -> dict:
    if os.path.exists(WEBHOOK_CONFIG_FILE):
        try:
            with open(WEBHOOK_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_webhook_config(cfg: dict):
    os.makedirs("states", exist_ok=True)
    with open(WEBHOOK_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

PROCESSED_PACKAGING_ORDERS_FILE = "states/processed_packaging_orders.json"

def _load_processed_packaging_orders() -> dict:
    if os.path.exists(PROCESSED_PACKAGING_ORDERS_FILE):
        try:
            with open(PROCESSED_PACKAGING_ORDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_processed_packaging_orders(data: dict):
    os.makedirs("states", exist_ok=True)
    with open(PROCESSED_PACKAGING_ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)




