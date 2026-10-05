import os
import json
import logging
import threading
import time
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import create_engine, select, func, delete
from sqlalchemy.orm import Session
from backend.product_name_overrides import resolve_product_name, is_placeholder_name

logger = logging.getLogger(__name__)

# Анти-шторм релогинов MC: после неудачной попытки логина стор сидит в
# cooldown (см. _refresh_mc_session) — иначе каждый тикер каждого воркера
# перезапускает браузер-логин хоть всю ночь. Значение — env, чтобы можно
# было ужесточить/отключить (0 = как раньше, без cooldown) без деплоя кода.
_MC_RELOGIN_COOLDOWN_SECONDS = int(os.getenv("MC_RELOGIN_COOLDOWN_SECONDS", "900"))
_mc_relogin_fail_at: dict = {}  # store_id -> time.monotonic() последней неудачи

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
                # Пул задан явно и согласован с размером дефолтного
                # executor'а лупа (APP_THREAD_WORKERS в backend/main.py):
                # каждый поток, работающий через asyncio.to_thread, берёт
                # отсюда по соединению, и пул меньше числа потоков превращает
                # ускорение в новую очередь — уже на checkout'е, с дефолтным
                # ожиданием 30с. Дефолт SQLAlchemy — 5+10.
                sync_engine = create_engine(
                    sync_url, pool_pre_ping=True, pool_size=10, max_overflow=14
                )
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
                    "status": r.status,
                    # Возраст оверрайда нужен _apply_overrides, чтобы не класть
                    # позавчерашнюю локальную цену поверх только что выкачанной
                    # из Kaspi (см. разбор там же).
                    "updated_at": r.updated_at,
                }
        except Exception as e:
            logger.debug(f"DB load overrides error: {e}")
        finally:
            session.close()
    return overrides

# Сколько Kaspi отдаёт в каталоге старую цену после нашей записи. Оверрайд
# моложе отсечки минус это время ещё «в пути»: каталог его не видел. Без запаса
# 04–05.10.2026 синхронизация через 2–3 минуты после цикла демпинга стирала
# только что записанную цену — так терялось ~20% ходов, в т.ч. 4 из 10
# охлаждений рынка (их можно делать раз в сутки) и подъёмы вида 2445 → 3200.
# Протухнуть навсегда (баг 605 ₸ от 13.08) оверрайд всё равно не может:
# следующая синхронизация через полчаса его уберёт.
OVERRIDE_KASPI_LAG_MINUTES = 15


def _override_cutoff(fetched_at):
    import datetime as _dt
    return fetched_at - _dt.timedelta(minutes=OVERRIDE_KASPI_LAG_MINUTES)


def _purge_stale_overrides(store_id: str, before) -> int:
    """
    Удалить оверрайды старше `before` — момента, когда каталог был выкачан из
    Kaspi. Возвращает число удалённых.

    Оверрайд — это локальный слепок «мы только что поменяли цену/остаток, а
    Kaspi ещё не знает». Как только Kaspi отдал своё состояние, слепок старше
    этого ответа устарел по определению: либо изменение доехало (и лежит в
    свежих данных), либо было отменено. Держать его дальше нельзя — именно
    так позавчерашние 605 ₸ пережили десяток синхронизаций и заставляли
    демпер каждый цикл «чинить» несуществующее отклонение.

    Барьера свежести в _apply_overrides для этого мало: цикл демпинга
    накладывает оверрайды на уже сохранённый каталог и никакой отсечки не
    знает, так что протухшая запись всплыла бы там снова. Чистим у источника.
    """
    session = _get_sync_db_session()
    if not session or not before:
        return 0
    try:
        from backend.db.models import ProductOverrideDB
        deleted = session.query(ProductOverrideDB).filter(
            ProductOverrideDB.store_id == store_id,
            ProductOverrideDB.updated_at < _override_cutoff(before),
        ).delete(synchronize_session=False)
        session.commit()
        if deleted:
            logger.info(f"🧹 [Overrides] Удалено {deleted} оверрайдов старше синхронизации с Kaspi (store {store_id}).")
        return deleted
    except Exception as e:
        logger.error(f"Ошибка очистки протухших оверрайдов: {e}")
        session.rollback()
        return 0
    finally:
        session.close()


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

def _override_for_product(product: dict, overrides: dict):
    """
    Оверрайд для товара: сперва по merchantSku (артикул), потом по Kaspi sku.

    Оверрайды пишутся ДВУМЯ разными путями и в разных ключах: демпер кладёт их
    по merchantSku (executor.py::save_override_func получает sku_key), а ручное
    редактирование цены из панели — по Kaspi sku (числовой masterSku, его шлёт
    фронт). В таблице один столбец sku, так что у одного товара может быть по
    записи в каждом ключе. Берём merchantSku-запись как основную (тот же
    приоритет, что у движка — radar.py:216), но если запись по sku СВЕЖЕЕ,
    выигрывает она: значит пользователь только что поменял цену руками.
    """
    m_sku = product.get("merchantSku")
    by_msku = overrides.get(m_sku) if m_sku else None
    by_sku = overrides.get(product.get("sku"))
    if by_msku and by_sku:
        a = by_msku.get("updated_at")
        b = by_sku.get("updated_at")
        if a and b:
            return by_sku if b > a else by_msku
        return by_msku
    return by_msku or by_sku


def _apply_overrides(products: list, store_id: str, *, fresh_from_kaspi_at=None) -> list:
    """
    Наложить локальные оверрайды на каталог.

    fresh_from_kaspi_at — момент, когда этот список был выкачан из Kaspi.
    Если он передан, оверрайды СТАРШЕ него не применяются: их цена уже учтена
    Kaspi (либо отменена там), и класть её поверх свежих данных — значит
    бесконечно показывать движку несуществующую цену.

    Так и было до 2026-08-13: оверрайд по masterSku 111881073 = 605 ₸ от 12
    августа ложился поверх честных 595 ₸ из Kaspi при каждой синхронизации.
    Демпер видел 605, решал «надо 595», ставил намерение, offer_sync сверялся
    с Kaspi, видел там уже 595 и гасил запись вхолостую — и так каждый цикл.
    На проде это давало 90-100 фантомных «изменений» за цикл на ~100 SKU.
    """
    overrides = _load_overrides(store_id)
    for p in products:
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
            
        ov = _override_for_product(p, overrides)
        if ov:
            ov_at = ov.get("updated_at")
            if fresh_from_kaspi_at and ov_at and ov_at < _override_cutoff(fresh_from_kaspi_at):
                continue
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

def _json_or_none(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


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
                    "city_code": r.city_code or "750000000",
                    "guard": _json_or_none(r.guard_config),
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
                    city_code=str(cfg.get("city_code", "750000000")),
                    guard_config=json.dumps(cfg["guard"], ensure_ascii=False) if cfg.get("guard") else None,
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
                # Защиту трогаем, только если её прислали: импорт PriceFeed и
                # прочие писатели конфига про неё не знают и не должны стирать.
                if "guard" in cfg:
                    db_obj.guard_config = json.dumps(cfg["guard"], ensure_ascii=False) if cfg["guard"] else None
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
                sku_recipes = json.loads(r.sku_recipes) if r.sku_recipes else {}
                groups[r.group_id] = {
                    "group_id": r.group_id,
                    "group_name": r.group_name,
                    "shared_stock": _stock_num(r.shared_stock),
                    "oversold": _stock_num(r.oversold or 0),
                    "sku_recipes": sku_recipes,
                    "linked_skus": linked_skus,
                    "external_code": r.external_code,
                    "is_active": r.is_active,
                    "stop_qty": r.stop_qty if r.stop_qty is not None else 5,
                    "stop_notified": bool(r.stop_notified),
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

def _stock_num(v):
    """Остаток группы: дробный, но целое отдаём как int (см. sku_recipes.num)."""
    from backend.services.sku_recipes import num
    return num(v or 0)


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
            recipes_str = json.dumps(g["sku_recipes"], ensure_ascii=False) if g.get("sku_recipes") else None
            if not db_obj:
                db_obj = MasterStockGroupDB(
                    group_id=gid,
                    store_id=store_id,
                    group_name=g.get("group_name", "Группа"),
                    shared_stock=_stock_num(g.get("shared_stock", 0)),
                    oversold=_stock_num(g.get("oversold", 0)),
                    sku_recipes=recipes_str,
                    linked_skus=linked_str,
                    external_code=g.get("external_code"),
                    is_active=bool(g.get("is_active", True)),
                    stop_qty=int(g.get("stop_qty", 5)),
                    stop_notified=bool(g.get("stop_notified", False)),
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
                db_obj.shared_stock = _stock_num(g.get("shared_stock", 0))
                db_obj.oversold = _stock_num(g.get("oversold", 0))
                # Словарь без ключа (собран не из _load_master_stock_groups) не
                # должен молча стирать наборы группы.
                if "sku_recipes" in g:
                    db_obj.sku_recipes = recipes_str
                db_obj.linked_skus = linked_str
                db_obj.external_code = g.get("external_code")
                db_obj.is_active = bool(g.get("is_active", True))
                db_obj.stop_qty = int(g.get("stop_qty", 5))
                db_obj.stop_notified = bool(g.get("stop_notified", False))
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

# Размер пачки для IN-выборок и bulk-upsert'а отметок. Postgres держит максимум
# 65535 связанных параметров на запрос, у строки отметки их четыре — 500 строк
# на пачку оставляют запас на порядок.
_PROCESSED_ORDERS_CHUNK = 500


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _load_processed_orders_by_kind(store_id: str, kind: str, keys=None) -> dict:
    """{order_id: payload} из ProcessedOrderDB этого магазина+kind ('radar',
    'packaging' или 'stock' — держит их независимыми).

    `keys` задан — выбираются ТОЛЬКО эти ключи (IN-запрос пачками). Отметок у
    живого магазина десятки тысяч (10 343 'packaging' + 10 265 'stock' у
    Бархана на 16.09), а вызывающему коду почти всегда нужны отметки по
    заказам одной текущей выборки. Полный скан таблицы стоил ~2с и делался на
    каждый тик авто-списания, а в claim_stock_deduction — на КАЖДУЮ позицию
    заказа, и всё это внутри store-лока.

    `keys=None` — прежнее поведение, весь kind целиком.
    """
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import ProcessedOrderDB
        result = {}

        def _collect(rows):
            for r in rows:
                try:
                    result[r.order_id] = json.loads(r.payload)
                except Exception:
                    result[r.order_id] = {}

        base = select(ProcessedOrderDB).where(
            ProcessedOrderDB.store_id == store_id, ProcessedOrderDB.kind == kind
        )
        if keys is None:
            _collect(session.scalars(base).all())
        else:
            wanted = [k for k in dict.fromkeys(str(k) for k in keys) if k]
            for chunk in _chunks(wanted, _PROCESSED_ORDERS_CHUNK):
                _collect(session.scalars(base.where(ProcessedOrderDB.order_id.in_(chunk))).all())
        return result
    finally:
        session.close()


def _processed_orders_upsert(session):
    """Диалектный INSERT ... ON CONFLICT DO UPDATE, либо None для остальных БД."""
    try:
        dialect = session.get_bind().dialect.name
    except Exception:
        return None
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
        return _insert
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
        return _insert
    return None


def _save_processed_orders_by_kind(orders: dict, store_id: str, kind: str):
    """Записывает ТОЛЬКО переданные отметки, одним bulk-upsert'ом на пачку.

    Здесь был цикл session.get() по каждому ключу словаря, а вызывающий код
    передавал весь накопленный словарь отметок целиком. У Бархана это 10 343
    строки, то есть 10 343 отдельных SELECT+UPDATE за один тик авто-списания —
    и всё внутри store-лока: py-spy на живом проде показал 68 секунд из 70 в
    этой функции. Пока она работала, лок не отпускался, и любая правка
    Мастер-Группы из кабинета стояла в очереди за ним (PUT p95 = 48с, PATCH
    остатка — 24с, хотя сам PATCH в Kaspi не ходит вообще), а веерная рассылка
    остатка после продажи стартовала через две минуты после самого списания.

    Функция по-прежнему НЕ авторитетна: ключи, которых нет в `orders`, не
    трогаются. Вызывающий код обязан передавать дельту, а не весь словарь.
    """
    if not orders:
        return
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import ProcessedOrderDB
        rows = [
            {
                "store_id": store_id,
                "kind": kind,
                "order_id": str(order_id),
                "payload": json.dumps(payload, ensure_ascii=False),
            }
            for order_id, payload in orders.items()
        ]
        insert = _processed_orders_upsert(session)
        if insert is not None:
            for chunk in _chunks(rows, _PROCESSED_ORDERS_CHUNK):
                stmt = insert(ProcessedOrderDB).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["store_id", "kind", "order_id"],
                    set_={"payload": stmt.excluded.payload},
                )
                session.execute(stmt)
        else:
            # Экзотический диалект (в проде не встречается) — старый путь.
            for row in rows:
                existing = session.get(ProcessedOrderDB, (store_id, kind, row["order_id"]))
                if not existing:
                    session.add(ProcessedOrderDB(**row))
                else:
                    existing.payload = row["payload"]
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting processed orders ({kind}) to DB: {e}")
        session.rollback()
    finally:
        session.close()


# Отметки старше этого срока не нужны никому: самая широкая выборка заказов —
# полный свип авто-списания на 13 дней (FULL_SWEEP_WINDOW_DAYS), так что запас
# тут больше чем трёхкратный. Срок НЕЛЬЗЯ опускать ниже окна свипа: удалить
# отметку по заказу, который ещё попадает в выборку, значит списать его
# остаток второй раз.
PROCESSED_ORDERS_RETENTION_DAYS = 45

# Поля с временем в payload — у каждого kind своё (см. stock_sync_worker).
_PROCESSED_ORDERS_TIME_FIELDS = ("deducted_at", "claimed_at", "processed_at")


def _prune_processed_orders(
    store_id: str, kind: str, older_than_days: int = PROCESSED_ORDERS_RETENTION_DAYS
) -> int:
    """Удаляет отметки старше `older_than_days`. Возвращает число удалённых.

    Таблица растёт вечно (у Бархана 20 608 строк за время жизни), а нужна она
    только чтобы не списать один и тот же заказ дважды. Строка без разбираемой
    метки времени НЕ удаляется — молча потерять бронь хуже, чем хранить лишнее.
    """
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return 0
    try:
        from backend.db.models import ProcessedOrderDB
        cutoff = _dt.datetime.now() - _dt.timedelta(days=older_than_days)
        rows = session.scalars(
            select(ProcessedOrderDB).where(
                ProcessedOrderDB.store_id == store_id, ProcessedOrderDB.kind == kind
            )
        ).all()
        stale = []
        for r in rows:
            try:
                payload = json.loads(r.payload)
            except Exception:
                continue
            raw = next(
                (payload.get(f) for f in _PROCESSED_ORDERS_TIME_FIELDS if payload.get(f)), None
            )
            if not raw:
                continue
            try:
                # Оба формата, которые пишет воркер: ISO и "%Y-%m-%d %H:%M:%S"
                # (fromisoformat разбирает и его).
                stamp = _dt.datetime.fromisoformat(str(raw))
            except ValueError:
                continue
            if stamp < cutoff:
                stale.append(r.order_id)
        if not stale:
            return 0
        for chunk in _chunks(stale, _PROCESSED_ORDERS_CHUNK):
            session.execute(
                delete(ProcessedOrderDB).where(
                    ProcessedOrderDB.store_id == store_id,
                    ProcessedOrderDB.kind == kind,
                    ProcessedOrderDB.order_id.in_(chunk),
                )
            )
        session.commit()
        return len(stale)
    except Exception as e:
        logger.error(f"Error pruning processed orders ({kind}) for store {store_id}: {e}")
        session.rollback()
        return 0
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
    этого стора и помечает store.status='ACTIVE' — КРОМЕ магазинов со
    статусом 'PAUSED' (снятых с обслуживания оператором): им куки
    обновляются, а статус остаётся. Используется сразу после успешного
    логина/автообновления сессии, чтобы источником правды при
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
        # Куки сохраняем всегда (это хранение данных магазина), но статус
        # PAUSED не трогаем: он означает «оператор снял магазин с
        # обслуживания», и авто-перевыпуск сессии не повод вернуть его во
        # все фоновые воркеры (list_active_stores фильтрует по этому полю).
        if store.status != "PAUSED":
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

    Анти-шторм: после НЕУДАЧНОГО релогина следующий запуск для этого стора
    блокируется на MC_RELOGIN_COOLDOWN_SECONDS (env, дефолт 900с). Без него
    каждый 30-45с тикер всех воркеров перезапускал бы браузер-логин круглосуточно,
    если пароль сменился/аккаунт залочен — до блокировки аккаунта на стороне Kaspi.
    Успешный путь не затронут: cooldown ставится только фактом неудачи.
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
            from backend.services import ops_metrics

            now_mono = time.monotonic()
            last_fail = _mc_relogin_fail_at.get(store_id)
            if last_fail is not None and (now_mono - last_fail) < _MC_RELOGIN_COOLDOWN_SECONDS:
                ops_metrics.inc("mc_relogin_skipped_cooldown")
                logger.warning(
                    f"Релогин MC для стора {store_id!r} пропущен: предыдущая попытка провалилась "
                    f"{int(now_mono - last_fail)}с назад (cooldown {_MC_RELOGIN_COOLDOWN_SECONDS}с). "
                    "Следующая попытка — по истечении cooldown или после ручного входа."
                )
                return False
            from backend.services.playwright_worker import worker, Command
            success, msg, cookies = worker.send_cmd(Command.LOGIN, {"email": email, "password": password})
            if success:
                logger.info(f"Сессия Kaspi MC автоматически обновлена (истёкшие куки перевыпущены) для стора {store_id!r}.")
                if store_id is not None and cookies:
                    _save_mc_cookies_for_store(store_id, cookies)
            else:
                logger.error(f"Автообновление сессии Kaspi MC не удалось: {msg}")
            if success:
                _mc_relogin_fail_at.pop(store_id, None)
                ops_metrics.mc_relogin_ok(store_id)
            else:
                _mc_relogin_fail_at[store_id] = time.monotonic()
                ops_metrics.mc_relogin_failed(store_id, str(msg)[:200])
            return bool(success)
        except Exception as e:
            logger.error(f"Автообновление сессии Kaspi MC вызвало исключение: {e}")
            _mc_relogin_fail_at[store_id] = time.monotonic()
            try:
                from backend.services import ops_metrics
                ops_metrics.mc_relogin_failed(store_id, str(e)[:200])
            except Exception:
                pass
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

def _load_processed_packaging_orders(store_id: str, keys=None) -> dict:
    return _load_processed_orders_by_kind(store_id, "packaging", keys=keys)

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


def _list_sales_orders_since(store_id: str, since: Optional["datetime"] = None, limit: int = 100) -> list:
    """[Public API 1С/ERP] Завершённые заказы этого магазина, отсортированные
    по created_at по возрастанию — для пагинации курсором ?since=<ISO дата
    последнего полученного заказа> на стороне 1С/ERP-клиента."""
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import SalesOrderDB
        stmt = select(SalesOrderDB).where(SalesOrderDB.store_id == store_id)
        if since is not None:
            stmt = stmt.where(SalesOrderDB.created_at > since)
        stmt = stmt.order_by(SalesOrderDB.created_at.asc()).limit(max(1, min(limit, 500)))
        rows = session.scalars(stmt).all()
        return [
            {
                "order_internal_id": r.order_internal_id,
                "order_code": r.order_code,
                "created_at": r.created_at,
                "total_price": r.total_price,
                "status": r.status,
                "state": r.state,
                "entries": json.loads(r.entries_payload) if r.entries_payload else [],
            }
            for r in rows
        ]
    finally:
        session.close()


def resolve_external_code(store_id: str, sku: Optional[str], merchant_sku: Optional[str] = None) -> Optional[str]:
    """[Public API 1С/ERP] Артикул 1С (external_code) Мастер-Группы, в
    linked_skus которой входит этот sku/merchantSku заказа. None, если товар
    ни в одну Мастер-Группу не привязан — 1С тогда сама сопоставляет по sku."""
    if not sku and not merchant_sku:
        return None
    groups = _load_master_stock_groups(store_id=store_id)
    for g in groups.values():
        linked = g.get("linked_skus") or []
        if (sku and sku in linked) or (merchant_sku and merchant_sku in linked):
            return g.get("external_code")
    return None


def _load_product_tags(store_id: str, skus: Optional[list] = None) -> dict:
    """
    Метки товаров магазина: {sku: [тег, ...]}.

    skus=None — весь магазин (кабинет), список — только эти артикулы (печать
    спрашивает ровно по составу партии, а не тянет всю таблицу).
    """
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import ProductTagDB
        stmt = select(ProductTagDB).where(ProductTagDB.store_id == store_id)
        if skus is not None:
            wanted = [str(s).strip() for s in skus if str(s or "").strip()]
            if not wanted:
                return {}
            stmt = stmt.where(ProductTagDB.sku.in_(wanted))
        out: dict = {}
        for row in session.scalars(stmt).all():
            out.setdefault(row.sku, []).append(row.tag)
        for tags in out.values():
            tags.sort()
        return out
    except Exception as e:
        logger.error(f"Не удалось загрузить метки товаров: {e}")
        return {}
    finally:
        session.close()


def _save_product_tags(store_id: str, sku: str, tags: list) -> list:
    """Полностью заменяет набор меток товара. Возвращает то, что осталось."""
    sku = str(sku or "").strip()
    if not sku:
        return []
    cleaned, seen = [], set()
    for raw in tags or []:
        tag = str(raw or "").strip()
        # Верхняя граница — ширина колонки в БД; на этикетке столько всё равно
        # не поместится, но резать чужой текст молча тут не наше дело.
        if not tag or len(tag) > 60:
            continue
        low = tag.lower()
        if low not in seen:
            seen.add(low)
            cleaned.append(tag)

    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import ProductTagDB
        session.query(ProductTagDB).filter(
            ProductTagDB.store_id == store_id, ProductTagDB.sku == sku
        ).delete(synchronize_session=False)
        for tag in cleaned:
            session.add(ProductTagDB(store_id=store_id, sku=sku, tag=tag))
        session.commit()
        return cleaned
    except Exception as e:
        session.rollback()
        logger.error(f"Не удалось сохранить метки товара {sku}: {e}")
        return []
    finally:
        session.close()


def _list_store_tags(store_id: str) -> list:
    """Все метки магазина с числом товаров — справочник собирается из фактов."""
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import ProductTagDB
        rows = session.execute(
            select(ProductTagDB.tag, func.count(ProductTagDB.sku))
            .where(ProductTagDB.store_id == store_id)
            .group_by(ProductTagDB.tag)
        ).all()
        return sorted(
            [{"tag": tag, "products": count} for tag, count in rows],
            key=lambda e: (-e["products"], e["tag"]),
        )
    except Exception as e:
        logger.error(f"Не удалось загрузить список меток: {e}")
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


def _load_quality_metrics_history(store_id: str, days: int = 30) -> list:
    """Ряд дневных снапшотов за period — рейтинг магазина (%) и ОБЩЕЕ число
    отзывов (сумма гистограммы 1-5 звёзд, qualityControl.ratingWithStatistics.
    statistics — уже скрапится ночным воркером в raw_payload, см.
    kaspi_quality_metrics.py). Для дашборда рядом с воронкой рассылки «Отзыв»:
    сама рассылка не атрибутируется к конкретным отзывам (Kaspi не даёт эту
    связку, см. review_campaign.py) — тренд числа отзывов тут даёт только
    косвенную корреляцию, не причинность."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import KaspiQualityMetricsDailyDB
        since = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
        rows = session.scalars(
            select(KaspiQualityMetricsDailyDB)
            .where(KaspiQualityMetricsDailyDB.store_id == store_id, KaspiQualityMetricsDailyDB.date >= since)
            .order_by(KaspiQualityMetricsDailyDB.date.asc())
        ).all()
        result = []
        for r in rows:
            review_count = None
            try:
                stats = (json.loads(r.raw_payload or "{}").get("rating") or {}).get("statistics") or None
                if stats:
                    review_count = sum(
                        stats.get(k) or 0
                        for k in ("oneCount", "twoCount", "threeCount", "fourCount", "fiveCount")
                    )
            except Exception:
                review_count = None
            result.append({"date": r.date, "rating_percentage": r.rating_percentage, "review_count": review_count})
        return result
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
            row.url = (str(it["url"])[:500] if it.get("url") else None)
            if "guard" in it:
                row.guard_json = json.dumps(it["guard"], ensure_ascii=False, default=str) if it["guard"] else None
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
                "url": r.url,
                "guard": _json_or_none(r.guard_json),
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()


def _load_store_guard(store_id: str) -> dict:
    """Общие настройки защиты от оттеснения магазина (guard.GuardConfig как dict).
    Пусто — магазин защиту не настраивал (она выключена)."""
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import StoreDumpingGuardDB
        row = session.get(StoreDumpingGuardDB, store_id)
        return (_json_or_none(row.payload) or {}) if row else {}
    except Exception as e:
        logger.debug(f"DB load store guard error: {e}")
        return {}
    finally:
        session.close()


def _save_store_guard(store_id: str, payload: dict):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import StoreDumpingGuardDB
        import datetime as _dt
        row = session.get(StoreDumpingGuardDB, store_id)
        if not row:
            row = StoreDumpingGuardDB(store_id=store_id)
            session.add(row)
        row.payload = json.dumps(payload or {}, ensure_ascii=False)
        row.updated_at = _dt.datetime.utcnow()
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting store guard: {e}")
        session.rollback()
        raise
    finally:
        session.close()


PRICE_WAR_MOVES_RETENTION_DAYS = 45


def _record_price_war_moves(moves: list, store_id: str):
    """moves: [{sku, our_old, our_new, action, rival: {merchant_id, name,
    price, reviews, rating, intercity} | None}] — каждое наше изменение цены
    демпингом. Заодно подрезает историю старше PRICE_WAR_MOVES_RETENTION_DAYS."""
    if not moves:
        return
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import PriceWarMoveDB
        from sqlalchemy import delete
        import datetime as _dt
        now = _dt.datetime.utcnow()
        for m in moves:
            rival = m.get("rival") or {}
            session.add(PriceWarMoveDB(
                store_id=store_id,
                sku=str(m["sku"])[:100],
                created_at=now,
                our_old=float(m["our_old"]),
                our_new=float(m["our_new"]),
                rival_id=(str(rival["merchant_id"])[:64] if rival.get("merchant_id") else None),
                rival_name=(str(rival["name"])[:300] if rival.get("name") else None),
                rival_price=rival.get("price"),
                rival_reviews=rival.get("reviews"),
                rival_rating=rival.get("rating"),
                rival_intercity=rival.get("intercity"),
                action=(str(m.get("action") or "")[:50] or None),
            ))
        session.execute(delete(PriceWarMoveDB).where(
            PriceWarMoveDB.store_id == store_id,
            PriceWarMoveDB.created_at < now - _dt.timedelta(days=PRICE_WAR_MOVES_RETENTION_DAYS),
        ))
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting price war moves: {e}")
        session.rollback()
    finally:
        session.close()


def _load_price_war_moves(store_id: str, hours: int = 72, sku: str = None) -> dict:
    """История наших ходов за последние `hours` часов: {sku: [ход, ...]}
    по возрастанию времени."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import PriceWarMoveDB
        since = _dt.datetime.utcnow() - _dt.timedelta(hours=hours)
        stmt = select(PriceWarMoveDB).where(
            PriceWarMoveDB.store_id == store_id, PriceWarMoveDB.created_at >= since
        )
        if sku:
            stmt = stmt.where(PriceWarMoveDB.sku == sku)
        out: dict = {}
        for r in session.scalars(stmt.order_by(PriceWarMoveDB.created_at)).all():
            out.setdefault(r.sku, []).append({
                "created_at": r.created_at,
                "our_old": r.our_old,
                "our_new": r.our_new,
                "rival_id": r.rival_id,
                "rival_name": r.rival_name,
                "rival_price": r.rival_price,
                "rival_reviews": r.rival_reviews,
                "rival_rating": r.rival_rating,
                "rival_intercity": r.rival_intercity,
                "action": r.action,
            })
        return out
    except Exception as e:
        logger.debug(f"DB load price war moves error: {e}")
        return {}
    finally:
        session.close()


# Сколько дней «ждёт решения» продавец, которого радар больше не видит рядом
# с нами, прежде чем пропасть из очереди менеджера (строка остаётся).
# Вопрос, который цикл демпинга перестал задавать (продавец ушёл или решение
# больше не меняет цену), через столько часов пропадает из «Ждут решения».
GUARD_ASK_STALE_HOURS = 6


def _decision_row_to_dict(r) -> dict:
    return {
        "id": r.id, "sku": r.sku, "merchant_id": r.merchant_id, "merchant_name": r.merchant_name,
        "product_name": r.product_name, "status": r.status, "price": r.price, "our_price": r.our_price,
        "price_without": r.price_without, "url": r.url,
        "first_seen": r.first_seen.isoformat() if r.first_seen else None,
        "last_seen": r.last_seen.isoformat() if r.last_seen else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "decided_by": r.decided_by,
    }


def _load_guard_decisions(store_id: str) -> dict:
    """Решения менеджера по продавцам без рейтинга: {sku: {merchant_id: статус}}.
    ASK тоже возвращается — движок считает его «не решено»."""
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from backend.db.models import PriceWarRivalDecisionDB
        out: dict = {}
        rows = session.scalars(select(PriceWarRivalDecisionDB).where(
            PriceWarRivalDecisionDB.store_id == store_id)).all()
        for r in rows:
            out.setdefault(r.sku, {})[r.merchant_id] = r.status
        return out
    except Exception as e:
        logger.debug(f"DB load guard decisions error: {e}")
        return {}
    finally:
        session.close()


def _upsert_guard_asks(asks: list, store_id: str) -> list:
    """asks: [{sku, product_name, merchant_id, merchant_name, price, our_price}]
    из цикла демпинга. Новые пары «товар + продавец» создаются со статусом
    ASK и возвращаются (их надо показать менеджеру), у существующих
    обновляются цена и last_seen. Решённые не трогаются."""
    if not asks:
        return []
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import PriceWarRivalDecisionDB
        import datetime as _dt
        now = _dt.datetime.utcnow()
        existing = {
            (r.sku, r.merchant_id): r
            for r in session.scalars(select(PriceWarRivalDecisionDB).where(
                PriceWarRivalDecisionDB.store_id == store_id)).all()
        }
        created = []
        for a in asks:
            mid = str(a.get("merchant_id") or "")[:64]
            sku = str(a.get("sku") or "")[:100]
            if not mid or not sku:
                continue
            row = existing.get((sku, mid))
            if row is None:
                row = PriceWarRivalDecisionDB(
                    store_id=store_id, sku=sku, merchant_id=mid, status="ASK",
                    first_seen=now, last_seen=now,
                )
                session.add(row)
                existing[(sku, mid)] = row
                created.append((row, a))
            row.last_seen = now
            row.merchant_name = (str(a.get("merchant_name") or "")[:300] or row.merchant_name)
            row.product_name = (str(a.get("product_name") or "")[:500] or row.product_name)
            row.price = a.get("price")
            row.our_price = a.get("our_price")
            row.price_without = a.get("price_without")
            row.url = (str(a.get("url") or "")[:500] or row.url)
        session.commit()
        # Для уведомления — вместе с подробностями из цикла (место, доставка, min_price).
        return [{**a, **_decision_row_to_dict(r)} for r, a in created]
    except Exception as e:
        logger.error(f"Error persisting guard asks: {e}")
        session.rollback()
        return []
    finally:
        session.close()


def _list_guard_decisions(store_id: str, pending_only: bool = False, limit: int = 300) -> list:
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import PriceWarRivalDecisionDB
        import datetime as _dt
        stmt = select(PriceWarRivalDecisionDB).where(PriceWarRivalDecisionDB.store_id == store_id)
        if pending_only:
            stale = _dt.datetime.utcnow() - _dt.timedelta(hours=GUARD_ASK_STALE_HOURS)
            stmt = stmt.where(PriceWarRivalDecisionDB.status == "ASK",
                              PriceWarRivalDecisionDB.last_seen >= stale)
            stmt = stmt.order_by(PriceWarRivalDecisionDB.first_seen.desc())
        else:
            stmt = stmt.where(PriceWarRivalDecisionDB.status != "ASK")
            stmt = stmt.order_by(PriceWarRivalDecisionDB.decided_at.desc())
        return [_decision_row_to_dict(r) for r in session.scalars(stmt.limit(limit)).all()]
    except Exception as e:
        logger.debug(f"DB list guard decisions error: {e}")
        return []
    finally:
        session.close()


def _get_guard_decision(decision_id: int):
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import PriceWarRivalDecisionDB
        row = session.get(PriceWarRivalDecisionDB, int(decision_id))
        return {**_decision_row_to_dict(row), "store_id": row.store_id} if row else None
    finally:
        session.close()


def _set_guard_decision(decision_id: int, status: str, decided_by: str, store_id: str = None):
    """Записывает решение. store_id задан — строка обязана принадлежать этому
    магазину (кабинет); None — вызов из Телеграма, где магазин проверяется
    по самой строке. Возвращает dict строки или None, если строки нет."""
    session = _get_sync_db_session()
    if not session:
        return None
    try:
        from backend.db.models import PriceWarRivalDecisionDB
        import datetime as _dt
        row = session.get(PriceWarRivalDecisionDB, int(decision_id))
        if row is None or (store_id is not None and row.store_id != store_id):
            return None
        row.status = status
        row.decided_at = None if status == "ASK" else _dt.datetime.utcnow()
        row.decided_by = None if status == "ASK" else (decided_by or "")[:200]
        session.commit()
        return {**_decision_row_to_dict(row), "store_id": row.store_id}
    except Exception as e:
        logger.error(f"Error saving guard decision: {e}")
        session.rollback()
        raise
    finally:
        session.close()



def _guard_blacklist_merchant(store_id: str, merchant_id: str) -> None:
    """«Не учитывать нигде»: продавец в чёрный список защиты магазина
    (кабинет и кнопка в Телеграме)."""
    from backend.dumper import guard
    settings = guard.GuardConfig.from_dict(_load_store_guard(store_id)).to_dict()
    if merchant_id not in settings["blacklist"]:
        settings["blacklist"] = settings["blacklist"] + [merchant_id]
        _save_store_guard(store_id, guard.clean_guard_settings(settings))

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
    client_text: str | None = None,
    reply_text: str | None = None,
    order_code: str | None = None,
    detail: str | None = None,
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
            client_text=client_text,
            reply_text=reply_text,
            order_code=(order_code or None) and str(order_code)[:30],
            detail=(detail or None) and str(detail)[:200],
            created_at=_dt.datetime.utcnow(),
        ))
        session.commit()
    except Exception as e:
        logger.error(f"Error persisting chat response log to DB: {e}")
        session.rollback()
    finally:
        session.close()


_ESCALATION_KINDS = ("inbound_escalated_trigger", "inbound_escalated_ai")


def _load_bot_journal(store_id: str, days: int = 7, kind: str | None = None, limit: int = 100) -> dict:
    """Журнал автоответчика: последние записи с текстами + сводка по видам.

    escalation_rate — доля переданных менеджеру среди всех входящих, которые
    бот обработал (ИИ-ответ, шаблон, эскалация). Записи до миграции 045 без
    текстов, поэтому сводка считается по всем строкам, а список — как есть."""
    import datetime as _dt
    empty = {"period_days": days, "items": [], "by_kind": {}, "handled": 0, "escalated": 0, "escalation_rate": 0.0}
    session = _get_sync_db_session()
    if not session:
        return empty
    try:
        from backend.db.models import ChatResponseLogDB
        from sqlalchemy import func
        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)
        base = (ChatResponseLogDB.store_id == store_id, ChatResponseLogDB.created_at >= since)

        by_kind = {
            k: c for k, c in session.execute(
                select(ChatResponseLogDB.kind, func.count()).where(*base).group_by(ChatResponseLogDB.kind)
            ).all()
        }
        handled = sum(by_kind.get(k, 0) for k in ("inbound_reply", "inbound_logistics_template", *_ESCALATION_KINDS))
        escalated = sum(by_kind.get(k, 0) for k in _ESCALATION_KINDS)

        q = select(ChatResponseLogDB).where(*base)
        if kind == "escalated":
            q = q.where(ChatResponseLogDB.kind.in_(_ESCALATION_KINDS))
        elif kind:
            q = q.where(ChatResponseLogDB.kind == kind)
        rows = session.scalars(q.order_by(ChatResponseLogDB.created_at.desc()).limit(limit)).all()
        items = [{
            "id": r.id,
            "created_at": r.created_at.isoformat() + "Z",
            "group_id": r.group_id,
            "kind": r.kind,
            "client_text": r.client_text,
            "reply_text": r.reply_text,
            "order_code": r.order_code,
            "detail": r.detail,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
        } for r in rows]
        return {
            "period_days": days,
            "items": items,
            "by_kind": by_kind,
            "handled": handled,
            "escalated": escalated,
            "escalation_rate": round(escalated / handled, 3) if handled else 0.0,
        }
    finally:
        session.close()


def _load_bot_sent_texts(store_id: str, group_id: str) -> tuple[set, "object | None"]:
    """Тексты, которые в этот чат отправил сам бот (ответы ИИ, шаблоны,
    исходящие кампании), и момент, с которого ответы бота журналируются с
    текстом. Нужно, чтобы в истории для модели отличить ответы менеджера от
    своих: сообщение магазина после этого момента, которого нет среди наших
    текстов, написал человек."""
    session = _get_sync_db_session()
    if not session:
        return set(), None
    try:
        from backend.db.models import ChatResponseLogDB, OutboundQueueDB
        from sqlalchemy import func
        texts = set(session.scalars(
            select(ChatResponseLogDB.reply_text).where(
                ChatResponseLogDB.store_id == store_id,
                ChatResponseLogDB.group_id == group_id,
                ChatResponseLogDB.reply_text.isnot(None),
            )
        ).all())
        texts |= set(session.scalars(
            select(OutboundQueueDB.message_text).where(
                OutboundQueueDB.store_id == store_id,
                OutboundQueueDB.group_id == group_id,
                OutboundQueueDB.status == "sent",
            )
        ).all())
        tracking_since = session.scalar(
            select(func.min(ChatResponseLogDB.created_at)).where(
                ChatResponseLogDB.store_id == store_id,
                ChatResponseLogDB.reply_text.isnot(None),
            )
        )
        return {t.strip() for t in texts if t}, tracking_since
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


def _load_sales_daily(store_id: str, days: int = 30) -> list:
    """Выручка и число заказов по дням (UTC-дата, как в _load_sales_summary)
    за последние N дней — без разбора entries_payload. Для виджетов, которым
    нужен только дневной ряд (score/heatmap): полная сводка на 180 днях
    стоила ~3.5с CPU на каждый опрос дашборда."""
    import datetime as _dt
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from backend.db.models import SalesOrderDB
        since = _dt.datetime.utcnow() - _dt.timedelta(days=days)
        rows = session.execute(
            select(SalesOrderDB.created_at, SalesOrderDB.total_price)
            .where(SalesOrderDB.store_id == store_id, SalesOrderDB.created_at >= since)
        ).all()
        daily: dict = {}
        for created_at, total_price in rows:
            day_key = created_at.strftime("%Y-%m-%d")
            bucket = daily.setdefault(day_key, {"date": day_key, "revenue": 0.0, "orders": 0})
            bucket["revenue"] += total_price
            bucket["orders"] += 1
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
        # Только нужные столбцы, без ORM-объектов: у Бархана ~48k заказов за
        # 180 дней, и гидратация SalesOrderDB занимала больше половины из ~3.5с.
        rows = session.execute(
            select(SalesOrderDB.created_at, SalesOrderDB.total_price, SalesOrderDB.entries_payload)
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
            if p.get("sku") and p.get("name") and not is_placeholder_name(p.get("name"), p.get("sku"))
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
                name = resolve_product_name(sku, e.get("name"), catalog_names=catalog_names) or sku
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

