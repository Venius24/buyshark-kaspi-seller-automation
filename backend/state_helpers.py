import os
import json
import logging
import threading
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Кэш engine для Postgres-пути (прод) — см. _get_sync_db_session().
_sync_engine_cache = {"engine": None, "url": None}
_sync_engine_lock = threading.Lock()

# Категория 1: RateLimitExecutorWorker.execute_updates теперь обрабатывает
# несколько SKU параллельно (ThreadPoolExecutor) — этот лок защищает
# ресурс, который раньше никогда не вызывался конкурентно.
_overrides_file_lock = threading.Lock()  # см. _save_override()

# Де-глобализация Playwright: лок НЕ общий на всё приложение, а один на
# store_id — конкурентные 401 у РАЗНЫХ сторов должны релогиниться реально
# параллельно (в этом весь смысл пула потоков в playwright_worker.py), а
# конкурентные 401 у ОДНОГО стора (несколько SKU словили 401 одновременно)
# всё ещё должны сериализоваться в один relogin, а не бить по Kaspi N раз.
_mc_refresh_locks: dict = {}
_mc_refresh_locks_guard = threading.Lock()

def _get_mc_refresh_lock(store_id) -> threading.Lock:
    with _mc_refresh_locks_guard:
        if store_id not in _mc_refresh_locks:
            _mc_refresh_locks[store_id] = threading.Lock()
        return _mc_refresh_locks[store_id]

SESSION_FILE = "states/session.json"

def _get_sync_db_session():
    """Возвращает синхронную сессию SQLAlchemy для безопасного сохранения состояния в БД.

    Postgres (прод): engine кэшируется на уровне модуля, а schema-check
    (Base.metadata.create_all) выполняется только один раз, при первом
    создании engine. Раньше это гоняло полный DDL-reflection на КАЖДЫЙ
    вызов этой функции (а она вызывается 1-9+ раз за один HTTP-запрос) —
    десятки лишних round-trip'ов к БД вхолостую. Alembic и так владеет
    схемой (deploy.sh шаг 2 гоняет `alembic upgrade head`).

    SQLite (dev/тесты): поведение сознательно не меняется — engine создаётся
    заново на каждый вызов. DATABASE_URL здесь обычно относительный путь
    (`sqlite+aiosqlite:///./kaspi_saas.db`), и часть тестов полагается на
    то, что он резолвится от ТЕКУЩЕГО os.chdir() на момент вызова (см.
    tests/test_login_router_store_scoped.py и др. — каждый тест меняет CWD
    на свой tempfile.mkdtemp() без переопределения DATABASE_URL). Закэшированный
    engine держал бы одно открытое соединение на файл первого вызвавшего
    теста и был бы виден (и ломал бы изоляцию) всем последующим тестам.
    """
    try:
        from backend.db.database import ASYNC_DB_URL, Base
        sync_url = ASYNC_DB_URL.replace("+asyncpg", "").replace("+aiosqlite", "")

        if "sqlite" in sync_url:
            sync_engine = create_engine(sync_url, connect_args={"check_same_thread": False})
            Base.metadata.create_all(sync_engine)
            return Session(sync_engine)

        with _sync_engine_lock:
            if _sync_engine_cache["engine"] is None or _sync_engine_cache["url"] != sync_url:
                sync_engine = create_engine(sync_url, pool_pre_ping=True)
                Base.metadata.create_all(sync_engine)
                _sync_engine_cache["engine"] = sync_engine
                _sync_engine_cache["url"] = sync_url
            return Session(_sync_engine_cache["engine"])
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

def _load_overrides(store_id: str) -> dict:
    """
    Слайс 7: store_id обязателен — легаси-фоллбек на общий states/products_overrides.json
    (мешал оверрайды всех магазинов в одну кучу) убран целиком. Пустой
    результат значит "у этого магазина ещё нет оверрайдов", а не сигнал
    падать на общий файл.
    """
    session = _get_sync_db_session()
    overrides = {}
    if session:
        try:
            from backend.db.models import ProductOverrideDB
            stmt = select(ProductOverrideDB).where(ProductOverrideDB.store_id == store_id)
            rows = session.scalars(stmt).all()
            for r in rows:
                overrides[r.sku] = {
                    "price_num": r.price_num,
                    "stock": r.stock,
                    "brand": r.brand,
                    "status": r.status
                }
        except Exception as e:
            logger.debug(f"DB load overrides error: {e}")
        finally:
            session.close()
    return overrides

def _save_override(
    sku: str,
    price: int,
    stock: int,
    brand: Optional[str] = None,
    status: Optional[str] = None,
    *,
    store_id: str,
):
    # Категория 1: RateLimitExecutorWorker.execute_updates может звать это
    # из нескольких потоков одновременно (по одному на SKU). Лок сериализует
    # доступ к общему in-process ресурсу (сама БД-транзакция ниже уже
    # атомарна), не трогая медленные параллельные сетевые вызовы к Kaspi
    # выше по стеку.
    with _overrides_file_lock:
        ov_data = {
            "price_num": price,
            "stock": stock,
            "brand": brand,
            "status": status or ("Активен" if stock > 0 else "Архив")
        }

        session = _get_sync_db_session()
        if not session:
            return
        try:
            from backend.db.models import ProductOverrideDB
            db_obj = session.execute(
                select(ProductOverrideDB).where(
                    ProductOverrideDB.sku == sku, ProductOverrideDB.store_id == store_id
                )
            ).scalar_one_or_none()
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

def _load_products(store_id: str) -> list:
    """Каталог этого магазина из StoreProductDB. Пустой результат значит
    "у этого магазина ещё нет синхронизированного каталога"."""
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import StoreProductDB
        row = session.get(StoreProductDB, store_id)
        if not row:
            return []
        try:
            return json.loads(row.payload)
        except Exception:
            return []
    finally:
        session.close()

def _save_products(products: list, store_id: str):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import StoreProductDB
        payload = json.dumps(products, ensure_ascii=False)
        row = session.get(StoreProductDB, store_id)
        if not row:
            row = StoreProductDB(store_id=store_id, payload=payload)
            session.add(row)
        else:
            row.payload = payload
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting products to DB for store {store_id}: {e}")
        session.rollback()
    finally:
        session.close()

def _apply_overrides(products: list, store_id: str) -> list:
    overrides = _load_overrides(store_id)
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

def _load_dumping_configs(store_id: str) -> dict:
    """Конфиги демпинга этого магазина. Пустой результат значит "у этого
    магазина ещё нет конфигов демпинга"."""
    session = _get_sync_db_session()
    configs = {}
    if session:
        try:
            from backend.db.models import DumpingConfigDB
            stmt = select(DumpingConfigDB).where(DumpingConfigDB.store_id == store_id)
            rows = session.scalars(stmt).all()
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
                    "dump_step": float(r.dump_step or 2.0),
                    "enabled": bool(r.enabled),
                    "mode": r.mode or "TOP_1",
                    "target_position": int(r.target_position or 1),
                    "max_delivery_days": r.max_delivery_days,
                    "min_seller_rating": r.min_seller_rating,
                    "same_city_only": bool(r.same_city_only),
                    "blacklisted_merchants": blacklisted,
                    "city_code": r.city_code or "750000000"
                }
        except Exception as e:
            logger.debug(f"DB load dumping configs error: {e}")
        finally:
            session.close()
    return configs

def dumping_config_for_product(product: dict, configs: dict):
    """Конфиг демпинга для товара: сперва по merchantSku, потом по Kaspi sku.

    Один и тот же оффер известен под двумя идентификаторами, а конфиги
    пишутся по merchantSku (импорт из PriceFeed, панель демпинга). Движок
    матчит именно в этом порядке (backend/dumper/radar.py:216), и всё, что
    показывает пользователю состояние демпинга, обязано матчить так же —
    иначе панель врёт, что демпинг выключен, хотя движок его крутит."""
    m_sku = product.get("merchantSku")
    if m_sku and m_sku in configs:
        return configs[m_sku]
    return configs.get(product.get("sku"))


def _save_dumping_configs(configs: dict, store_id: str):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import DumpingConfigDB
        for sku, cfg in configs.items():
            db_obj = session.execute(
                select(DumpingConfigDB).where(
                    DumpingConfigDB.sku == sku, DumpingConfigDB.store_id == store_id
                )
            ).scalar_one_or_none()
            blacklisted_str = json.dumps(cfg.get("blacklisted_merchants") or [], ensure_ascii=False)
            if not db_obj:
                db_obj = DumpingConfigDB(
                    sku=sku,
                    store_id=store_id,
                    min_price=float(cfg.get("min_price", 0.0)),
                    max_price=float(cfg.get("max_price", 0.0)),
                    dump_step=float(cfg.get("dump_step", 2.0)),
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
                db_obj.min_price = float(cfg.get("min_price", 0.0))
                db_obj.max_price = float(cfg.get("max_price", 0.0))
                db_obj.dump_step = float(cfg.get("dump_step", 2.0))
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

def _load_master_stock_groups(store_id: str) -> dict:
    """Мастер-Группы остатка этого магазина. Пустой результат означает "у
    этого магазина ещё нет групп"."""
    session = _get_sync_db_session()
    groups = {}
    if session:
        try:
            from backend.db.models import MasterStockGroupDB
            stmt = select(MasterStockGroupDB).where(MasterStockGroupDB.store_id == store_id)
            rows = session.scalars(stmt).all()
            for r in rows:
                linked_skus = json.loads(r.linked_skus) if r.linked_skus else []
                dumping_cfg = json.loads(r.dumping_config) if r.dumping_config else None
                dumping_excluded_skus = json.loads(r.dumping_excluded_skus) if r.dumping_excluded_skus else []
                sync_logs = json.loads(r.sync_logs) if r.sync_logs else []
                groups[r.group_id] = {
                    "group_id": r.group_id,
                    "group_name": r.group_name,
                    "shared_stock": r.shared_stock,
                    "linked_skus": linked_skus,
                    "external_code": r.external_code,
                    "is_active": r.is_active,
                    "stop_qty": r.stop_qty if r.stop_qty is not None else 5,
                    "dumping_config": dumping_cfg,
                    "dumping_excluded_skus": dumping_excluded_skus,
                    "status": r.status,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                    "last_synced_at": r.last_synced_at,
                    "last_sync_log": r.last_sync_log,
                    "sync_logs": sync_logs
                }
        except Exception as e:
            logger.debug(f"DB load master stock groups error: {e}")
        finally:
            session.close()
    return groups

def _save_master_stock_groups(groups: dict, store_id: str):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import MasterStockGroupDB
        for gid, g in groups.items():
            db_obj = session.get(MasterStockGroupDB, gid)
            linked_str = json.dumps(g.get("linked_skus") or [], ensure_ascii=False)
            dump_cfg_str = json.dumps(g.get("dumping_config"), ensure_ascii=False) if g.get("dumping_config") else None
            dump_excl_str = json.dumps(g.get("dumping_excluded_skus") or [], ensure_ascii=False)
            logs_str = json.dumps(g.get("sync_logs") or [], ensure_ascii=False)
            if not db_obj:
                db_obj = MasterStockGroupDB(
                    group_id=gid,
                    store_id=store_id,
                    group_name=g.get("group_name", "Группа"),
                    shared_stock=int(g.get("shared_stock", 0)),
                    linked_skus=linked_str,
                    external_code=g.get("external_code"),
                    is_active=bool(g.get("is_active", True)),
                    stop_qty=int(g.get("stop_qty", 5)),
                    dumping_config=dump_cfg_str,
                    dumping_excluded_skus=dump_excl_str,
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
                db_obj.stop_qty = int(g.get("stop_qty", 5))
                db_obj.dumping_config = dump_cfg_str
                db_obj.dumping_excluded_skus = dump_excl_str
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

def _delete_master_stock_group(group_id: str, store_id: str) -> bool:
    """
    Удалить Мастер-Группу из БД. Возвращает True, если строка была удалена.

    Отдельная функция, а не "сохранить словарь без неё":
    _save_master_stock_groups делает ТОЛЬКО upsert по ключам переданного
    словаря и ничего не удаляет — из-за этого удаление группы в кабинете
    возвращало "успешно удалена", но строка оставалась в БД и группа
    возвращалась при следующей загрузке списка. Делать save авторитетным
    (стирать всё, чего нет в словаре) опаснее: любой вызов с частичным
    словарём молча снёс бы остальные группы магазина.

    store_id в условии обязателен — чтобы чужой group_id нельзя было удалить
    из другого магазина.
    """
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import MasterStockGroupDB
        row = session.get(MasterStockGroupDB, group_id)
        if not row or row.store_id != store_id:
            return False
        session.delete(row)
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error deleting master stock group {group_id}: {e}")
        session.rollback()
        return False
    finally:
        session.close()

def _load_processed_orders_by_kind(store_id: str, kind: str) -> dict:
    """{order_id: payload} из ProcessedOrderDB этого магазина+kind ('radar'
    или 'packaging' — держит их независимыми)."""
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import ProcessedOrderDB
        rows = session.scalars(
            select(ProcessedOrderDB).where(
                ProcessedOrderDB.store_id == store_id, ProcessedOrderDB.kind == kind
            )
        ).all()
        result = {}
        for r in rows:
            try:
                result[r.order_id] = json.loads(r.payload)
            except Exception:
                result[r.order_id] = {}
        return result
    finally:
        session.close()

def _save_processed_orders_by_kind(orders: dict, store_id: str, kind: str):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import ProcessedOrderDB
        for order_id, payload in orders.items():
            row = session.get(ProcessedOrderDB, (store_id, kind, order_id))
            payload_json = json.dumps(payload, ensure_ascii=False)
            if not row:
                session.add(ProcessedOrderDB(store_id=store_id, kind=kind, order_id=order_id, payload=payload_json))
            else:
                row.payload = payload_json
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting processed orders ({kind}) to DB: {e}")
        session.rollback()
    finally:
        session.close()

def _load_processed_orders(store_id: str) -> dict:
    return _load_processed_orders_by_kind(store_id, "radar")

def _save_processed_orders(orders: dict, store_id: str):
    _save_processed_orders_by_kind(orders, store_id, "radar")

def _get_store_status(store_id: str) -> Optional[str]:
    """
    Возвращает Store.status ('ACTIVE'/'COOKIE_EXPIRED'/'PAUSED') или None,
    если стор не найден. Это единственный источник правды, который реально
    обновляется по факту использования куки (health_check_worker,
    stores_v1.py verify/sync-cookies), в отличие от простого наличия
    расшифровываемых кук в _load_mc_cookies — куки могут физически лежать
    в БД и корректно расшифровываться, но быть уже недействительными на
    стороне Kaspi, пока их кто-то реально не проверит.
    """
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import Store
        store = session.get(Store, store_id)
        return store.status if store else None
    finally:
        session.close()

def _load_mc_cookies(store_id: Optional[str] = None) -> dict:
    """
    store_id=None (легаси, дев-стенд без Store в БД): нескоуп-чтение —
    ровно текущее поведение, проверяются root 'state.json',
    'states/session.json', 'states/state.json'.

    store_id given: читает ИСКЛЮЧИТЕЛЬНО Store.encrypted_cookies этого
    стора из БД. Без фоллбека на общий файл — если куки этого стора нет
    или они не расшифровались, возвращается {} (значит "нужен повторный
    вход"), а не чужая/устаревшая глобальная сессия.
    """
    if store_id is not None:
        session = _get_sync_db_session()
        if not session:
            return {}
        try:
            from backend.db.models import Store
            from backend.security.crypto import decrypt_data
            store = session.get(Store, store_id)
            if not store or not store.encrypted_cookies:
                return {}
            cookie_str = decrypt_data(store.encrypted_cookies)
            if not cookie_str:
                return {}
            cookies = {}
            for part in cookie_str.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                name, _, value = part.partition("=")
                if name.strip() in ("mc-session", "mc-sid"):
                    cookies[name.strip()] = value.strip()
            return cookies if "mc-session" in cookies and "mc-sid" in cookies else {}
        finally:
            session.close()

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

def _save_mc_cookies_for_store(store_id: str, cookies: dict) -> bool:
    """
    Сохраняет куки mc-session/mc-sid (dict) в Store.encrypted_cookies для
    этого стора и помечает store.status='ACTIVE'. Используется сразу после
    успешного логина/автообновления сессии, чтобы источником правды при
    чтении (_load_mc_cookies(store_id=...)) была БД, а не общий файл.
    """
    if not cookies or "mc-session" not in cookies or "mc-sid" not in cookies:
        return False
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import Store
        from backend.security.crypto import encrypt_data
        import datetime
        store = session.get(Store, store_id)
        if not store:
            return False
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        store.encrypted_cookies = encrypt_data(cookie_str)
        store.status = "ACTIVE"
        store.last_sync_at = datetime.datetime.utcnow()
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения кук Kaspi MC для стора {store_id}: {e}")
        session.rollback()
        return False
    finally:
        session.close()

def _load_chat_cookies(store_id: Optional[str] = None) -> dict:
    """
    Отдельная от _load_mc_cookies сессия — Kaspi чат-API требует куки,
    полученные через телефон+SMS вход (idmc.shop.kaspi.kz), а не через
    обычный email+пароль вход в кабинет. Те же имена кук (mc-session,
    mc-sid), но из другого источника, поэтому отдельное поле/файл, чтобы
    не перезаписывать основную MC-сессию демпинга/заказов.

    store_id=None (легаси): 'state_chat.json' в корне (аналог state.json).
    store_id given: Store.encrypted_chat_cookies этого стора, без фоллбека.
    """
    if store_id is not None:
        session = _get_sync_db_session()
        if not session:
            return {}
        try:
            from backend.db.models import Store
            from backend.security.crypto import decrypt_data
            store = session.get(Store, store_id)
            if not store or not store.encrypted_chat_cookies:
                return {}
            cookie_str = decrypt_data(store.encrypted_chat_cookies)
            if not cookie_str:
                return {}
            cookies = {}
            for part in cookie_str.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                name, _, value = part.partition("=")
                if name.strip() in ("mc-session", "mc-sid"):
                    cookies[name.strip()] = value.strip()
            return cookies if "mc-session" in cookies and "mc-sid" in cookies else {}
        finally:
            session.close()

    if not os.path.exists("state_chat.json"):
        return {}
    try:
        with open("state_chat.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        cookies = {}
        if isinstance(data, dict):
            cookies_list = data.get("cookies", [])
            if isinstance(cookies_list, list):
                for c in cookies_list:
                    if isinstance(c, dict) and c.get("name") in ["mc-session", "mc-sid"]:
                        cookies[c["name"]] = c["value"]
        return cookies if "mc-session" in cookies and "mc-sid" in cookies else {}
    except Exception:
        return {}

def _save_chat_cookies_for_store(store_id: str, cookies: dict) -> bool:
    """Сохраняет куки чат-сессии (SMS-вход) в Store.encrypted_chat_cookies. Не трогает encrypted_cookies (основная MC-сессия)."""
    if not cookies or "mc-session" not in cookies or "mc-sid" not in cookies:
        return False
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import Store
        from backend.security.crypto import encrypt_data
        store = session.get(Store, store_id)
        if not store:
            return False
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        store.encrypted_chat_cookies = encrypt_data(cookie_str)
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения кук чата Kaspi для стора {store_id}: {e}")
        session.rollback()
        return False
    finally:
        session.close()

def _save_mc_credentials(email: str, password: str, store_id: Optional[str] = None):
    """
    Сохраняет email/пароль от кабинета Kaspi MC (в зашифрованном виде), чтобы
    при истечении сессионных куков можно было автоматически перелогиниться.

    store_id=None (легаси): в states/session.json, как было всегда.
    store_id given: в Store.encrypted_mc_email/encrypted_mc_password этого
    стора — креды разных сторов не перезаписывают друг друга.
    """
    from backend.security.crypto import encrypt_data
    if store_id is not None:
        session = _get_sync_db_session()
        if not session:
            return
        try:
            from backend.db.models import Store
            store = session.get(Store, store_id)
            if store:
                store.encrypted_mc_email = encrypt_data(email)
                store.encrypted_mc_password = encrypt_data(password)
                session.commit()
        except Exception as e:
            logger.error(f"Ошибка сохранения кред Kaspi MC для стора {store_id}: {e}")
            session.rollback()
        finally:
            session.close()
        return

    session = _load_session()
    session["mc_email_enc"] = encrypt_data(email)
    session["mc_password_enc"] = encrypt_data(password)
    _save_session(session)

def _load_mc_credentials(store_id: Optional[str] = None) -> Optional[tuple]:
    """Возвращает (email, password) сохранённых кред Kaspi MC, либо None, если не сохранены/не расшифровались.

    store_id=None (легаси): читает states/session.json, как было всегда.
    store_id given: читает Store.encrypted_mc_email/encrypted_mc_password
    этого стора из БД — без фоллбека на общий файл.
    """
    from backend.security.crypto import decrypt_data

    if store_id is not None:
        db_session = _get_sync_db_session()
        if not db_session:
            return None
        try:
            from backend.db.models import Store
            store = db_session.get(Store, store_id)
            if not store or not store.encrypted_mc_email or not store.encrypted_mc_password:
                return None
            email = decrypt_data(store.encrypted_mc_email)
            password = decrypt_data(store.encrypted_mc_password)
            if not email or not password:
                return None
            return email, password
        finally:
            db_session.close()

    session = _load_session()
    email_enc = session.get("mc_email_enc")
    password_enc = session.get("mc_password_enc")
    if not email_enc or not password_enc:
        return None
    email = decrypt_data(email_enc)
    password = decrypt_data(password_enc)
    if not email or not password:
        return None
    return email, password

def _refresh_mc_session(store_id: Optional[str] = None) -> bool:
    """
    Автоматически обновляет сессионные куки Kaspi MC (mc-session/mc-sid) через сохранённые
    email/пароль — вызывается, когда куки истекли (401) или отсутствуют. Возвращает True при успехе.

    store_id=None (легаси dev-бутстрап без Store в БД): глобальные креды/куки.
    store_id given: креды берутся из этого стора (без фоллбека — нет кред,
    значит нужен явный повторный вход, а не угадывание чужих кред). Свежие
    куки приходят от worker.send_cmd напрямую (не через общий файл — см.
    playwright_worker.py, де-глобализация) и сразу копируются в
    Store.encrypted_cookies ИМЕННО этого стора.
    """
    creds = _load_mc_credentials(store_id=store_id)
    if not creds:
        logger.warning(f"Автообновление сессии Kaspi MC невозможно: email/пароль не сохранены для стора {store_id!r} (нужен повторный вход).")
        return False
    email, password = creds
    # Лок per-store (не общий на всё приложение): несколько потоков одного
    # и того же стора могут словить 401 одновременно при параллельной
    # обработке SKU (Категория 1) — сериализуем ИХ в один relogin, а не
    # бьём по Kaspi несколько раз подряд. Разные сторы при этом релогинятся
    # реально параллельно (пул потоков в playwright_worker.py).
    with _get_mc_refresh_lock(store_id):
        try:
            from backend.services.playwright_worker import worker, Command
            success, msg, cookies = worker.send_cmd(Command.LOGIN, {"email": email, "password": password})
            if success:
                logger.info(f"Сессия Kaspi MC автоматически обновлена (истёкшие куки перевыпущены) для стора {store_id!r}.")
                if store_id is not None and cookies:
                    _save_mc_cookies_for_store(store_id, cookies)
            else:
                logger.error(f"Автообновление сессии Kaspi MC не удалось: {msg}")
            return bool(success)
        except Exception as e:
            logger.error(f"Автообновление сессии Kaspi MC вызвало исключение: {e}")
            return False

def _mc_api_request(method: str, url: str, store_id: Optional[str] = None, retry_on_401: bool = True, **kwargs):
    """
    Единая точка HTTP-запросов к mc.shop.kaspi.kz с текущими сессионными куками.
    При ответе 401 (истёкшая сессия) один раз пробует автообновить куки через
    _refresh_mc_session() (сохранённые email/пароль) и повторяет запрос.

    store_id=None (легаси) — нескоуп-куки, как было всегда. store_id given —
    куки читаются/обновляются исключительно для этого стора.
    """
    import requests
    cookies = _load_mc_cookies(store_id=store_id)
    resp = requests.request(method, url, cookies=cookies, **kwargs)
    if resp.status_code == 401 and retry_on_401:
        logger.warning(f"Kaspi MC вернул 401 для {url} — пробуем автообновить сессию...")
        if _refresh_mc_session(store_id=store_id):
            fresh_cookies = _load_mc_cookies(store_id=store_id)
            resp = requests.request(method, url, cookies=fresh_cookies, **kwargs)
    return resp

def _list_api_keys(store_id: str) -> dict:
    """API-ключи 1С/ERP этого магазина (ApiKeyDB)."""
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import ApiKeyDB
        rows = session.scalars(select(ApiKeyDB).where(ApiKeyDB.store_id == store_id)).all()
        return {
            r.id: {
                "name": r.name,
                "hash": r.key_hash,
                "prefix": r.prefix,
                "created_at": r.created_at,
                "last_used": r.last_used,
                "active": r.active,
            }
            for r in rows
        }
    finally:
        session.close()

def _create_api_key(store_id: str, key_id: str, name: str, key_hash: str, prefix: str, created_at: str) -> bool:
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import ApiKeyDB
        session.add(ApiKeyDB(
            id=key_id, store_id=store_id, name=name, key_hash=key_hash,
            prefix=prefix, created_at=created_at, last_used=None, active=True,
        ))
        session.commit()
        return True
    except Exception as e:
        logger.error(f"Error creating API key in DB: {e}")
        session.rollback()
        return False
    finally:
        session.close()

def _revoke_api_key(store_id: str, key_id: str) -> Optional[str]:
    """Удаляет ключ, ТОЛЬКО если он принадлежит этому store_id — иначе
    (не найден / чужой магазин) возвращает None, ничего не удаляя."""
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import ApiKeyDB
        row = session.get(ApiKeyDB, key_id)
        if not row or row.store_id != store_id:
            return None
        name = row.name
        session.delete(row)
        session.commit()
        return name
    except Exception as e:
        logger.error(f"Error revoking API key in DB: {e}")
        session.rollback()
        return None
    finally:
        session.close()

def _find_api_key_by_hash(key_hash: str) -> Optional[dict]:
    """Для публичного API (X-API-Key) — резолвит владельца (store_id) по
    хешу ключа, без стор-скоупа: сам ключ и есть предъявленный идентификатор
    доступа, скоуп его результата — задача вызывающего кода."""
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import ApiKeyDB
        row = session.scalars(
            select(ApiKeyDB).where(ApiKeyDB.key_hash == key_hash, ApiKeyDB.active == True)
        ).first()
        if not row:
            return None
        return {"key_id": row.id, "name": row.name, "store_id": row.store_id}
    finally:
        session.close()

def _touch_api_key_last_used(key_id: str, when: str):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import ApiKeyDB
        row = session.get(ApiKeyDB, key_id)
        if row:
            row.last_used = when
            session.commit()
    except Exception as e:
        logger.error(f"Error touching API key last_used: {e}")
        session.rollback()
    finally:
        session.close()

def _load_webhook_config(store_id: str) -> dict:
    """Конфиг вебхука этого магазина (WebhookConfigDB), секрет хранится
    зашифрованным (security.crypto), возвращается расшифрованным."""
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import WebhookConfigDB
        from backend.security.crypto import decrypt_data
        row = session.get(WebhookConfigDB, store_id)
        if not row:
            return {}
        return {
            "url": row.url,
            "secret": decrypt_data(row.encrypted_secret) if row.encrypted_secret else "",
            "enabled": row.enabled,
            "updated_at": row.updated_at,
        }
    finally:
        session.close()

def _save_webhook_config(cfg: dict, store_id: str):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import WebhookConfigDB
        from backend.security.crypto import encrypt_data
        secret_plain = (cfg.get("secret") or "").strip()
        secret_enc = encrypt_data(secret_plain) if secret_plain else None
        row = session.get(WebhookConfigDB, store_id)
        if not row:
            row = WebhookConfigDB(
                store_id=store_id, url=cfg.get("url", ""), encrypted_secret=secret_enc,
                enabled=cfg.get("enabled", True), updated_at=cfg.get("updated_at"),
            )
            session.add(row)
        else:
            row.url = cfg.get("url", "")
            row.encrypted_secret = secret_enc
            row.enabled = cfg.get("enabled", True)
            row.updated_at = cfg.get("updated_at")
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting webhook config to DB: {e}")
        session.rollback()
    finally:
        session.close()

def _load_processed_packaging_orders(store_id: str) -> dict:
    return _load_processed_orders_by_kind(store_id, "packaging")

def _save_processed_packaging_orders(data: dict, store_id: str):
    _save_processed_orders_by_kind(data, store_id, "packaging")

def _append_dumper_logs(events: list, store_id: str):
    """events: список {"timestamp", "message", "saved_margin"} — новые
    события демпинга этого магазина за один проход исполнителя."""
    if not events:
        return

    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import DumperLogDB
        import datetime as _dt
        for event in events:
            session.add(DumperLogDB(
                store_id=store_id,
                created_at=_dt.datetime.utcnow(),
                payload=json.dumps(event, ensure_ascii=False),
            ))
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting dumper logs to DB: {e}")
        session.rollback()
    finally:
        session.close()

def _load_dumper_logs(store_id: str, limit: int = 5) -> list:
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import DumperLogDB
        rows = session.scalars(
            select(DumperLogDB)
            .where(DumperLogDB.store_id == store_id)
            .order_by(DumperLogDB.id.desc())
            .limit(limit)
        ).all()
        result = []
        for r in rows:
            try:
                result.append(json.loads(r.payload))
            except Exception:
                pass
        return result
    finally:
        session.close()

def _load_analytics_snapshot(store_id: str) -> dict:
    """AnalyticsSnapshotDB этого магазина."""
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import AnalyticsSnapshotDB
        row = session.get(AnalyticsSnapshotDB, store_id)
        if not row:
            return {}
        try:
            return json.loads(row.payload)
        except Exception:
            return {}
    finally:
        session.close()

def _save_analytics_snapshot(data: dict, store_id: str):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import AnalyticsSnapshotDB
        payload = json.dumps(data, ensure_ascii=False)
        row = session.get(AnalyticsSnapshotDB, store_id)
        if not row:
            row = AnalyticsSnapshotDB(store_id=store_id, payload=payload)
            session.add(row)
        else:
            row.payload = payload
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting analytics snapshot to DB: {e}")
        session.rollback()
    finally:
        session.close()


def _upsert_sales_orders(orders: list, store_id: str, review_eligible: bool = True) -> list:
    """Апсертит завершённые заказы (см. KaspiAPI.get_completed_orders) в
    sales_orders. Возвращает список реально НОВЫХ (не int — вызывающий код
    логирует их количество). Идемпотентно по (store_id, order_internal_id) —
    безопасно перезапускать с пересекающимися окнами дат.

    review_eligible=False на холодном 90-дневном бэкафилле нового магазина —
    иначе ежедневный скан кампании «Отзыв» (backend/chatbot/review_campaign.py)
    разом поймал бы всю историю заказов в окно "N дней с момента ingested_at"."""
    if not orders:
        return []
    session = _get_sync_db_session()
    if not session:
        return []
    inserted = []
    try:
        from backend.db.models import SalesOrderDB
        import datetime as _dt
        for o in orders:
            internal_id = o.get("internalId")
            if not internal_id:
                continue
            existing = session.get(SalesOrderDB, (store_id, internal_id))
            created_at = _dt.datetime.utcfromtimestamp((o.get("creationDateMs") or 0) / 1000.0)
            if existing:
                existing.total_price = o.get("totalPrice", 0)
                existing.status = o.get("status", "")
                existing.state = o.get("state", "")
                existing.entries_payload = json.dumps(o.get("entries", []), ensure_ascii=False)
            else:
                session.add(SalesOrderDB(
                    store_id=store_id,
                    order_internal_id=internal_id,
                    order_code=o.get("code", ""),
                    created_at=created_at,
                    total_price=o.get("totalPrice", 0),
                    status=o.get("status", ""),
                    state=o.get("state", ""),
                    entries_payload=json.dumps(o.get("entries", []), ensure_ascii=False),
                    ingested_at=_dt.datetime.utcnow(),
                    review_eligible=review_eligible,
                ))
                inserted.append(o)
        session.commit()
        return inserted
    except Exception as e:
        logger.error(f"Error upserting sales orders to DB: {e}")
        session.rollback()
        return []
    finally:
        session.close()


def _get_latest_sales_order_date(store_id: str):
    """Дата самого свежего заказа, уже сохранённого для этого стора —
    воркер использует её, чтобы понять, откуда продолжать инкрементальный
    доскан (вместо полного бэкафилла на каждом тике)."""
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import SalesOrderDB
        return session.execute(
            select(func.max(SalesOrderDB.created_at)).where(SalesOrderDB.store_id == store_id)
        ).scalar()
    finally:
        session.close()


def _upsert_analytics_daily_history(data: dict, store_id: str):
    """Пишет/обновляет ОДНУ строку на (store_id, сегодняшняя дата UTC) —
    вызывается на каждом цикле демпинга (backend/dumper/__init__.py), но
    идемпотентно: повторные вызовы в тот же день просто перезаписывают
    сегодняшний снапшот последним значением, не плодя дубликаты."""
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import AnalyticsDailyHistoryDB
        import datetime as _dt
        today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
        row = session.get(AnalyticsDailyHistoryDB, (store_id, today))
        if not row:
            row = AnalyticsDailyHistoryDB(store_id=store_id, date=today)
            session.add(row)
        row.best_price_share = float(data.get("best_price_share", 0.0))
        row.winning_count = int(data.get("winning_count", 0))
        row.total_scanned = int(data.get("total_scanned", 0))
        row.total_saved_margin = float(data.get("total_saved_margin", 0.0))
        row.updated_at = _dt.datetime.utcnow()
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting analytics daily history to DB: {e}")
        session.rollback()
    finally:
        session.close()


def _load_analytics_history(store_id: str, days: int = 90) -> list:
    """Дневная история best_price_share/winning_count этого магазина за
    последние N дней — копится только с момента деплоя этой фичи, поэтому
    для новых сторов список может быть коротким/пустым."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import AnalyticsDailyHistoryDB
        since = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
        rows = session.scalars(
            select(AnalyticsDailyHistoryDB)
            .where(AnalyticsDailyHistoryDB.store_id == store_id, AnalyticsDailyHistoryDB.date >= since)
            .order_by(AnalyticsDailyHistoryDB.date.asc())
        ).all()
        return [
            {
                "date": r.date,
                "best_price_share": r.best_price_share,
                "winning_count": r.winning_count,
                "total_scanned": r.total_scanned,
                "total_saved_margin": r.total_saved_margin,
            }
            for r in rows
        ]
    finally:
        session.close()


def _quality_metrics_fresh(store_id: str, min_interval_hours: float) -> bool:
    """True если самый свежий снапшот показателей качества для стора моложе
    min_interval_hours — воркер использует это, чтобы не скрапить один и тот
    же стор чаще заданного интервала (см. kaspi_quality_metrics_worker.py)."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import KaspiQualityMetricsDailyDB
        row = session.scalars(
            select(KaspiQualityMetricsDailyDB)
            .where(KaspiQualityMetricsDailyDB.store_id == store_id)
            .order_by(KaspiQualityMetricsDailyDB.fetched_at.desc())
            .limit(1)
        ).first()
        if not row:
            return False
        age = _dt.datetime.utcnow() - row.fetched_at
        return age < _dt.timedelta(hours=min_interval_hours)
    finally:
        session.close()


def _upsert_quality_metrics_daily(store_id: str, data: dict):
    """Пишет/обновляет ОДНУ строку на (store_id, сегодняшняя дата UTC) с
    результатом ночного скрапа раздела Kaspi "Показатели качества"."""
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import KaspiQualityMetricsDailyDB
        import datetime as _dt
        today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
        row = session.get(KaspiQualityMetricsDailyDB, (store_id, today))
        if not row:
            row = KaspiQualityMetricsDailyDB(store_id=store_id, date=today)
            session.add(row)
        row.rating_percentage = data.get("rating_percentage")
        row.returns_percentage = data.get("returns_percentage")
        row.late_kd_percentage = data.get("late_kd_percentage")
        row.cancelled_percentage = data.get("cancelled_percentage")
        row.avg_chat_response_minutes = data.get("avg_chat_response_minutes")
        row.chat_response_rate_percent = data.get("chat_response_rate_percent")
        row.raw_payload = json.dumps(data.get("raw_payload") or {}, ensure_ascii=False)
        row.fetched_at = _dt.datetime.utcnow()
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting Kaspi quality metrics to DB: {e}")
        session.rollback()
    finally:
        session.close()


def _load_quality_metrics_latest(store_id: str) -> Optional[dict]:
    """Последний (самый свежий по дате) снапшот показателей качества Kaspi
    для этого стора, None если ночной скрап ещё ни разу не отрабатывал."""
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import KaspiQualityMetricsDailyDB
        row = session.scalars(
            select(KaspiQualityMetricsDailyDB)
            .where(KaspiQualityMetricsDailyDB.store_id == store_id)
            .order_by(KaspiQualityMetricsDailyDB.date.desc())
            .limit(1)
        ).first()
        if not row:
            return None
        return {
            "date": row.date,
            "rating_percentage": row.rating_percentage,
            "returns_percentage": row.returns_percentage,
            "late_kd_percentage": row.late_kd_percentage,
            "cancelled_percentage": row.cancelled_percentage,
            "avg_chat_response_minutes": row.avg_chat_response_minutes,
            "chat_response_rate_percent": row.chat_response_rate_percent,
            "raw_payload": json.loads(row.raw_payload or "{}"),
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        }
    finally:
        session.close()


def _upsert_price_war_snapshot(items: list, store_id: str):
    """Пишет по одной строке на SKU за цикл демпинга — piggyback на уже
    идущий запрос конкурентов в brain.py (см. коммент в price-war роутере),
    без единого лишнего похода в Kaspi API. items: [{sku, name, our_price,
    our_rank, competitors_count, best_competitor_price, best_competitor_name}]."""
    if not items:
        return
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import PriceWarSnapshotDB
        import datetime as _dt
        now = _dt.datetime.utcnow()
        for it in items:
            sku = it["sku"]
            row = session.get(PriceWarSnapshotDB, (store_id, sku))
            if not row:
                row = PriceWarSnapshotDB(store_id=store_id, sku=sku)
                session.add(row)
            row.name = str(it.get("name") or sku)[:500]
            row.our_price = float(it.get("our_price") or 0.0)
            row.our_rank = it.get("our_rank")
            row.competitors_count = int(it.get("competitors_count") or 0)
            row.best_competitor_price = it.get("best_competitor_price")
            row.best_competitor_name = (str(it["best_competitor_name"])[:300] if it.get("best_competitor_name") else None)
            row.updated_at = now
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting price war snapshot to DB: {e}")
        session.rollback()
    finally:
        session.close()


def _load_price_war_snapshot(store_id: str) -> list:
    """Текущий расклад демпинг-войны по всем отслеживаемым SKU этого магазина."""
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import PriceWarSnapshotDB
        rows = session.scalars(
            select(PriceWarSnapshotDB).where(PriceWarSnapshotDB.store_id == store_id)
        ).all()
        return [
            {
                "sku": r.sku,
                "name": r.name,
                "our_price": r.our_price,
                "our_rank": r.our_rank,
                "competitors_count": r.competitors_count,
                "best_competitor_price": r.best_competitor_price,
                "best_competitor_name": r.best_competitor_name,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()


def _append_competitor_encounters(encounters: list, store_id: str):
    """encounters: [{sku, competitor_name}] — одна запись за цикл на SKU, где
    мы не на 1-м месте. Копится с момента деплоя фичи (никакого backfill)."""
    if not encounters:
        return
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import CompetitorEncounterLogDB
        import datetime as _dt
        now = _dt.datetime.utcnow()
        for enc in encounters:
            session.add(CompetitorEncounterLogDB(
                store_id=store_id,
                sku=enc["sku"],
                competitor_name=str(enc["competitor_name"])[:300],
                created_at=now,
            ))
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting competitor encounters to DB: {e}")
        session.rollback()
    finally:
        session.close()


def _load_competitor_leaderboard(store_id: str, days: int = 30, limit: int = 10) -> list:
    """Кто чаще всего обходит нас в демпинге за последние N дней. Пусто, пока
    история не накопилась (фича не откатывается назад по времени)."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import CompetitorEncounterLogDB
        from sqlalchemy import func
        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)
        rows = session.execute(
            select(CompetitorEncounterLogDB.competitor_name, func.count().label("cnt"))
            .where(CompetitorEncounterLogDB.store_id == store_id, CompetitorEncounterLogDB.created_at >= since)
            .group_by(CompetitorEncounterLogDB.competitor_name)
            .order_by(func.count().desc())
            .limit(limit)
        ).all()
        return [{"name": name, "count": cnt} for name, cnt in rows]
    finally:
        session.close()


def _append_chat_response_log(
    store_id: str,
    group_id: str,
    kind: str = "inbound_reply",
    prompt_tier: str = "",
    model: str = "",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    billed_units: int = 1,
    credit_units_milli: int = 0,
):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import ChatResponseLogDB
        import datetime as _dt
        session.add(ChatResponseLogDB(
            store_id=store_id,
            group_id=group_id,
            kind=kind,
            prompt_tier=prompt_tier,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            billed_units=billed_units,
            credit_units_milli=credit_units_milli,
            created_at=_dt.datetime.utcnow(),
        ))
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting chat response log to DB: {e}")
        session.rollback()
    finally:
        session.close()


def _load_bot_usage_summary(store_id: str, days: int = 30) -> dict:
    """Сводка тарификации ИИ-бота за период: сколько было реальных вызовов ИИ
    (billed_units), сколько бесплатных логистических шаблонов, разбивка по kind.
    Фаза 2 — счётчик пока просто цифра в кабинете, без реального инвойса."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return {"total_billed_units": 0, "by_kind": {}, "period_days": days}
    try:
        from backend.db.models import ChatResponseLogDB
        from sqlalchemy import func
        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)
        rows = session.execute(
            select(ChatResponseLogDB.kind, func.count().label("cnt"), func.sum(ChatResponseLogDB.billed_units).label("units"))
            .where(ChatResponseLogDB.store_id == store_id, ChatResponseLogDB.created_at >= since)
            .group_by(ChatResponseLogDB.kind)
        ).all()
        by_kind = {kind: {"count": cnt, "units": int(units or 0)} for kind, cnt, units in rows}
        total_billed_units = sum(v["units"] for v in by_kind.values())
        return {"total_billed_units": total_billed_units, "by_kind": by_kind, "period_days": days}
    finally:
        session.close()


def _load_outbound_stats(store_id: str, days: int = 30) -> dict:
    """Сводка по кампаниям рассылок (Фаза 6) за период: по каждой campaign —
    сколько в каждом статусе, и "доля ответивших" среди реально отправленных
    (replied_at IS NOT NULL / sent). Честная метка, НЕ "конверсия в отзывы" —
    Kaspi не даёт связку "оставлен ли отзыв" с конкретным заказом/чатом, так
    что дальше факта ответа клиента в чате идти нельзя."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return {"period_days": days, "by_campaign": {}}
    try:
        from backend.db.models import OutboundQueueDB
        from sqlalchemy import func
        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)

        status_rows = session.execute(
            select(OutboundQueueDB.campaign, OutboundQueueDB.status, func.count().label("cnt"))
            .where(OutboundQueueDB.store_id == store_id, OutboundQueueDB.created_at >= since)
            .group_by(OutboundQueueDB.campaign, OutboundQueueDB.status)
        ).all()

        replied_rows = session.execute(
            select(OutboundQueueDB.campaign, func.count().label("cnt"))
            .where(
                OutboundQueueDB.store_id == store_id,
                OutboundQueueDB.created_at >= since,
                OutboundQueueDB.status == "sent",
                OutboundQueueDB.replied_at.isnot(None),
            )
            .group_by(OutboundQueueDB.campaign)
        ).all()
        replied_by_campaign = {campaign: cnt for campaign, cnt in replied_rows}

        skip_reason_rows = session.execute(
            select(OutboundQueueDB.campaign, OutboundQueueDB.skip_reason, func.count().label("cnt"))
            .where(
                OutboundQueueDB.store_id == store_id,
                OutboundQueueDB.created_at >= since,
                OutboundQueueDB.status == "skipped",
            )
            .group_by(OutboundQueueDB.campaign, OutboundQueueDB.skip_reason)
        ).all()
        skip_reasons_by_campaign: dict = {}
        for campaign, reason, cnt in skip_reason_rows:
            skip_reasons_by_campaign.setdefault(campaign, {})[reason or "неизвестно"] = cnt

        by_campaign: dict = {}
        for campaign, status, cnt in status_rows:
            entry = by_campaign.setdefault(campaign, {
                "total": 0, "sent": 0, "replied": 0, "reply_rate": 0.0,
                "pending": 0, "needs_first_contact": 0, "skipped": 0, "failed": 0,
                "skip_reasons": {},
            })
            entry["total"] += cnt
            if status in entry:
                entry[status] = cnt

        for campaign, entry in by_campaign.items():
            entry["replied"] = replied_by_campaign.get(campaign, 0)
            entry["reply_rate"] = round(entry["replied"] / entry["sent"], 3) if entry["sent"] else 0.0
            entry["skip_reasons"] = skip_reasons_by_campaign.get(campaign, {})

        return {"period_days": days, "by_campaign": by_campaign}
    finally:
        session.close()


def _load_chat_activity(store_id: str, days: int = 180) -> list:
    """Дневная агрегация числа авто-ответов чата: [{date, count}]."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import ChatResponseLogDB
        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)
        rows = session.scalars(
            select(ChatResponseLogDB)
            .where(ChatResponseLogDB.store_id == store_id, ChatResponseLogDB.created_at >= since)
        ).all()
        daily: dict = {}
        for r in rows:
            day_key = r.created_at.strftime("%Y-%m-%d")
            daily.setdefault(day_key, {"date": day_key, "count": 0})
            daily[day_key]["count"] += 1
        return sorted(daily.values(), key=lambda x: x["date"])
    finally:
        session.close()


def _load_sales_summary(store_id: str, days: int = 30) -> dict:
    """Реальная агрегация выручки/продаж из sales_orders за последние N дней:
    выручка и число заказов по дням, топ-SKU по выручке, средний чек."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return {"daily": [], "top_skus": [], "total_revenue": 0.0, "total_orders": 0, "avg_order_value": 0.0}
    try:
        from backend.db.models import SalesOrderDB
        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)
        rows = session.scalars(
            select(SalesOrderDB)
            .where(SalesOrderDB.store_id == store_id, SalesOrderDB.created_at >= since)
            .order_by(SalesOrderDB.created_at.asc())
        ).all()

        # Kaspi's order-entries API иногда не отдаёт offer.name/title (пропущенный
        # include) — kaspi_wrapper.py тогда пишет в entries_payload литерал "Товар"
        # как заглушку. Разные SKU с этой заглушкой выглядят как один и тот же товар,
        # заполняющий все топы. Подменяем заглушку реальным названием из каталога
        # магазина по SKU, если оно у нас есть.
        catalog_names = {
            p.get("sku"): p.get("name")
            for p in _load_products(store_id)
            if p.get("sku") and p.get("name")
        }

        daily: dict = {}
        sku_revenue: dict = {}
        total_revenue = 0.0
        for r in rows:
            day_key = r.created_at.strftime("%Y-%m-%d")
            bucket = daily.setdefault(day_key, {"date": day_key, "revenue": 0.0, "orders": 0})
            bucket["revenue"] += r.total_price
            bucket["orders"] += 1
            total_revenue += r.total_price
            try:
                entries = json.loads(r.entries_payload)
            except Exception:
                entries = []
            for e in entries:
                sku = e.get("sku") or "?"
                name = catalog_names.get(sku) or e.get("name") or sku
                entry = sku_revenue.setdefault(sku, {"sku": sku, "name": name, "revenue": 0.0, "quantity": 0})
                entry["revenue"] += e.get("totalPrice", 0) or 0
                entry["quantity"] += e.get("quantity", 0) or 0

        total_orders = len(rows)
        # Полный список по каждому SKU (не топ-10) — фронтенд сам сортирует/фильтрует
        # по выручке или количеству, лучшие/худшие товары, без доп. запросов.
        top_skus = sorted(sku_revenue.values(), key=lambda x: x["revenue"], reverse=True)

        return {
            "daily": sorted(daily.values(), key=lambda x: x["date"]),
            "top_skus": top_skus,
            "total_revenue": round(total_revenue, 2),
            "total_orders": total_orders,
            "avg_order_value": round(total_revenue / total_orders, 2) if total_orders else 0.0,
        }
    finally:
        session.close()

