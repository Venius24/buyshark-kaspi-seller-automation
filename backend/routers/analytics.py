"""
FastAPI router for Dashboard Analytics and Widget stats.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from datetime import date as _date, datetime, timedelta
from collections import OrderedDict
from sqlalchemy import select
import io
import json
import time
import logging
import threading
from backend.state_helpers import (
    _load_dumping_configs, dumping_config_for_product,
    _load_products, _load_analytics_snapshot, _load_dumper_logs,
    _load_sales_summary, _load_sales_daily, _load_analytics_history, _load_price_war_snapshot,
    _load_competitor_leaderboard, _load_chat_activity, _get_sync_db_session,
    _load_quality_metrics_latest, _load_quality_metrics_history,
)
from backend.dumper.config import _decode_base64_if_needed
from backend.product_name_overrides import resolve_product_name, is_placeholder_name
from backend.dumper.radar import _fetch_offers_sync
from backend.security.store_context import StoreContext, get_store_context
from backend.security.store_roles import store_section_guard
from backend.services.sales_export import build_sales_export_xlsx
from backend.services.stock_forecast import (
    DEFAULT_COVER_DAYS, DEFAULT_HORIZON_DAYS, DEFAULT_LEAD_TIME_DAYS, DEFAULT_SLOW_DAYS,
    DEFAULT_WINDOW_DAYS,
    build_purchase_order_xlsx, compute_stock_forecast,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(store_section_guard("overview"))])

# Казахстан не переходит на летнее время — фиксированный UTC+5 круглый год
# (тот же оффсет, что в chatbot/outbound.py ALMATY_TZ и billing/catalog.py
# BILLING_TZ_OFFSET_HOURS). Прод-сервер живёт в UTC, поэтому всё, что
# показывается продавцу «по часам»/«по дням», надо сдвигать вручную.
ALMATY_UTC_OFFSET_HOURS = 5

# Фронтенд поллит /api/analytics/widgets каждые 10 секунд, пока открыта вкладка
# Overview (useAnalyticsWidgets.ts). Без кэша это означало живой синхронный
# запрос к публичному API конкурентов Kaspi на каждый poll — до 360 запросов/час
# на один и тот же SKU, что похоже и стало причиной бана/рейт-лимита IP VPS
# Kaspi'ем (2026-07-29, подтверждено: 429 даже на kaspi.kz/shop/ с того же IP).
# Реальный демпинг-цикл (radar.py через background_dumping_loop) и так сканирует
# рынок раз в 5 минут — виджету дашборда не нужны данные свежее этого.
_WIDGET_OFFERS_CACHE_TTL_SECONDS = 120
_widget_offers_cache: dict = {}

# ABC/XYZ разбирает JSON всех заказов за период. Держим готовый ответ между
# двухминутными опросами страницы; ключ включает магазин и нормализованный период.
_ABC_XYZ_CACHE_TTL_SECONDS = 6 * 60 * 60
_ABC_XYZ_CACHE_MAX_ENTRIES = 128
_abc_xyz_cache: OrderedDict[tuple[str, int], tuple[float, dict]] = OrderedDict()
_abc_xyz_cache_lock = threading.Lock()


def _get_cached_abc_xyz(store_id: str, days: int) -> dict | None:
    key = (store_id, days)
    with _abc_xyz_cache_lock:
        cached = _abc_xyz_cache.get(key)
        if cached is None:
            return None
        if time.monotonic() >= cached[0]:
            del _abc_xyz_cache[key]
            return None
        _abc_xyz_cache.move_to_end(key)
        return cached[1]


def _cache_abc_xyz(store_id: str, days: int, result: dict) -> None:
    key = (store_id, days)
    with _abc_xyz_cache_lock:
        _abc_xyz_cache[key] = (time.monotonic() + _ABC_XYZ_CACHE_TTL_SECONDS, result)
        _abc_xyz_cache.move_to_end(key)
        if len(_abc_xyz_cache) > _ABC_XYZ_CACHE_MAX_ENTRIES:
            _abc_xyz_cache.popitem(last=False)

def _get_cached_offers(master_sku: str, city_code: str) -> list:
    cache_key = (master_sku, city_code)
    cached = _widget_offers_cache.get(cache_key)
    now = time.time()
    if cached and (now - cached[0]) < _WIDGET_OFFERS_CACHE_TTL_SECONDS:
        return cached[1]
    offers = _fetch_offers_sync(master_sku, city_code)
    _widget_offers_cache[cache_key] = (now, offers)
    return offers

@router.get("/widgets")
def get_dashboard_widgets(ctx: StoreContext = Depends(get_store_context)):
    """
    Returns live metrics for dashboard widgets including:
    - Buy Box share & saved margin
    - Live Price Radar events & competitor gaps
    - AI Chat Sentiment & Heatmap shell data
    - Store Pulse status
    """
    now = datetime.now()

    # 1. Load actual products & dumping configs
    products = _load_products(ctx.store_id)

    configs = _load_dumping_configs(store_id=ctx.store_id)
    
    # 2. Load or compute analytics summary
    analytics_data = _load_analytics_snapshot(ctx.store_id)


    # Реальный снэпшот пишет BrainWorker на каждом цикле демпинга (dumper/__init__.py).
    # Если снэпшота ещё нет (демпинг ни разу не запускался для этого стора) —
    # показываем честный ноль, а не выдуманные цифры: пользователь без активных
    # dumping-конфигов не может иметь ни "лидерства", ни "сохранённой маржи".
    total_scanned = analytics_data.get("total_scanned", 0)
    winning_count = analytics_data.get("winning_count", 0)
    best_price_share = analytics_data.get("best_price_share", 0.0)
    total_saved_margin = analytics_data.get("total_saved_margin", 0.0)

    if not total_scanned:
        enabled_skus = [sku for sku, cfg in configs.items() if cfg.get("enabled")]
        total_scanned = len(enabled_skus)
    
    # 3. Find first active product for live price radar
    active_product = None
    active_cfg = None
    for p in products:
        # Тот же приоритет merchantSku → sku, что и у движка: иначе живой радар
        # цены не находил ни одного демпингуемого товара и падал в фоллбек на
        # первый попавшийся товар каталога.
        p_cfg = dumping_config_for_product(p, configs)
        if p_cfg and p_cfg.get("enabled"):
            active_product = p
            active_cfg = p_cfg
            break
            
    # Fallback to any product if no active dumping config is enabled
    if not active_product and products:
        active_product = products[0]
        active_cfg = {"city_code": "750000000"}

    # Пусто, пока не найден реальный активный товар/оффер ниже — фронтенд
    # должен показать честное "нет данных", а не выдуманный товар/магазины.
    competitors = []
    top_product_name = None
    our_price = 0
    our_rank = None

    if active_product:
        top_product_name = resolve_product_name(
            active_product.get("merchantSku") or active_product.get("sku"),
            active_product.get("name"),
        ) or "Товар"
        our_price = int(active_product.get("price_num") or 0)
        
        raw_kaspi_sku = active_product.get("kaspiSku") or active_product.get("sku") or ""
        kaspi_master_sku = _decode_base64_if_needed(raw_kaspi_sku)
        city_code = active_cfg.get("city_code") or active_cfg.get("cityCode") or "750000000"
        
        if kaspi_master_sku:
            # Кэшируем на _WIDGET_OFFERS_CACHE_TTL_SECONDS — см. комментарий у кэша выше
            raw_offers = _get_cached_offers(kaspi_master_sku, city_code)
            if raw_offers:
                # НЕ подставляем чужой merchant_id (раньше был хардкод "20186986" —
                # реальный ID Barkhan: если у ctx нет merchant_id, конкурентный
                # оффер САМОГО Barkhan мог бы ошибочно подсветиться как "Мой
                # Магазин (Вы)" в дашборде другого пользователя). Пустая
                # строка не совпадёт ни с одним реальным merchantId.
                my_merchant_id = ctx.merchant_id or ""
                
                sorted_offers = []
                for offer in raw_offers:
                    m_id = str(offer.get("merchantId", ""))
                    m_name = offer.get("merchantName", "Конкурент")
                    price_val = offer.get("price")
                    if price_val is not None:
                        try:
                            price_num = float(str(price_val).replace(" ₸", "").replace(" ", "").strip())
                            sorted_offers.append({
                                "merchantId": m_id,
                                "name": m_name,
                                "price": price_num
                            })
                        except ValueError:
                            continue
                
                sorted_offers = sorted(sorted_offers, key=lambda x: x["price"])
                if sorted_offers:
                    competitors = []
                    best_price = sorted_offers[0]["price"]
                    for idx, offer in enumerate(sorted_offers, start=1):
                        is_self = offer["merchantId"] == str(my_merchant_id)
                        diff = int(offer["price"] - best_price)
                        competitors.append({
                            "name": "Мой Магазин (Вы)" if is_self else offer["name"],
                            "price": int(offer["price"]),
                            "rank": idx,
                            "diff": diff if not is_self else None,
                            "is_self": is_self
                        })
                        if is_self:
                            our_rank = idx

    # 4. Load recent sniper events (per-store, Слайс 5) — реальные записи из dumper_logs,
    # без выдуманного fallback-события, если демпинг ещё не сработал ни разу.
    radar_events = _load_dumper_logs(ctx.store_id, limit=5)

    return {
        "success": True,
        "timestamp": now.isoformat(),
        "analytics": {
            "best_price_share": best_price_share,
            "winning_count": winning_count,
            "total_scanned": total_scanned,
            "total_saved_margin": total_saved_margin,
            "active_competitors_tracked": len(configs),
        },
        "price_radar": {
            "top_product": {
                "name": top_product_name,
                "our_price": our_price,
                "our_rank": our_rank,
                "competitors": competitors
            },
            "recent_events": radar_events
        },
    }


@router.get("/sales")
def get_sales_stats(days: int = 30, ctx: StoreContext = Depends(get_store_context)):
    """
    Настоящая статистика продаж/выручки, посчитанная из sales_orders —
    реальной истории завершённых заказов Kaspi (backend/services/sales_sync_worker.py),
    а не выдуманных чисел. Пусто, пока воркер не сделал первый проход для этого стора.
    """
    days = max(1, min(days, 365))
    summary = _load_sales_summary(ctx.store_id, days=days)
    return {"success": True, "days": days, **summary}


@router.get("/sales-export")
def export_sales_stats(
    date_from: str,
    date_to: str,
    group_by: str = "card",
    periods_mode: bool = False,
    ctx: StoreContext = Depends(get_store_context),
):
    """
    Экспорт продаж за выбранный период в Excel — Фото | Артикул | Наименование |
    Код товара Kaspi | Продано, шт. | Средняя цена продажи | Дней с продажами
    (период) | Дней с первой продажи. См. backend/services/sales_export.py —
    там же объяснение, почему «дней с первой продажи» это прокси, а не
    настоящая дата добавления в каталог (её никто не хранит), а также что
    значат `group_by` (card/master_group) и `periods_mode` (по умолчанию
    False — одна строка на товар за весь диапазон без дублей; True —
    старое поведение с разбивкой на периоды продаж).
    """
    try:
        d_from = _date.fromisoformat(date_from)
        d_to = _date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="date_from/date_to должны быть в формате YYYY-MM-DD")
    if d_from > d_to:
        raise HTTPException(status_code=400, detail="date_from не может быть позже date_to")
    if (d_to - d_from).days > 366:
        raise HTTPException(status_code=400, detail="Период не может быть длиннее 366 дней")
    if group_by not in ("card", "master_group"):
        raise HTTPException(status_code=400, detail="group_by должен быть card или master_group")

    xlsx_bytes = build_sales_export_xlsx(ctx.store_id, d_from, d_to, group_by=group_by, periods_mode=periods_mode)
    filename = f"sales_{d_from.isoformat()}_{d_to.isoformat()}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/price-war")
def get_price_war_summary(ctx: StoreContext = Depends(get_store_context)):
    """
    Сводка демпинг-войны по ВСЕМ отслеживаемым SKU (не только первому) —
    данные из price_war_snapshots, которые пишутся piggyback'ом на обычный
    ~5-минутный цикл демпинга (backend/dumper/brain.py), без единого лишнего
    запроса к Kaspi. Пусто, пока демпинг не отработал ни один цикл.
    """
    items = _load_price_war_snapshot(ctx.store_id)
    ranked = [it["our_rank"] for it in items if it.get("our_rank")]
    winning_count = sum(1 for it in items if it.get("our_rank") == 1)
    total_in_war = len(items)

    items_sorted = sorted(items, key=lambda it: (it.get("our_rank") is None, -(it.get("our_rank") or 0)))

    return {
        "success": True,
        "summary": {
            "total_in_war": total_in_war,
            "avg_rank": round(sum(ranked) / len(ranked), 1) if ranked else 0,
            "winning_count": winning_count,
            "losing_count": total_in_war - winning_count,
        },
        "products": items_sorted,
    }


@router.get("/insights")
def get_insights(ctx: StoreContext = Depends(get_store_context)):
    """
    Пакет из нескольких второстепенных метрик дашборда в одном запросе, чтобы
    не плодить лишний polling: лидерборд конкурентов, покрытие каталога
    демпингом, активность продаж по часам/дням недели, активность авто-чата.
    Всё честно посчитано из реальных таблиц; пусто/ноль, пока история не накопилась.
    """
    import datetime as _dt

    # Лидерборд конкурентов (накапливается с деплоя price_war piggyback'а)
    leaderboard = _load_competitor_leaderboard(ctx.store_id, days=30, limit=10)

    # Покрытие каталога демпингом
    products = _load_products(ctx.store_id)
    configs = _load_dumping_configs(store_id=ctx.store_id)
    enabled_count = sum(1 for c in configs.values() if c.get("enabled"))
    total_products = len(products)
    catalog_coverage = {
        "total_products": total_products,
        "dumping_enabled": enabled_count,
        "coverage_pct": round((enabled_count / total_products * 100.0), 1) if total_products else 0.0,
    }

    # Продажи по часам/дням недели (за последние 90 дней, из sales_orders).
    # created_at в sales_orders — UTC (см. _upsert_sales_orders), а продавец
    # смотрит на график в алматинском времени. Без сдвига на +5 вся тепловая
    # карта была смещена на 5 часов назад: ночной провал 03-06 по Алматы
    # рисовался в 22-01, а вечерний пик 22:00 — в 17:00.
    session = _get_sync_db_session()
    hourly_grid = [[0] * 24 for _ in range(7)]  # [day_of_week 0=Пн..6=Вс][hour]
    if session:
        try:
            from backend.db.models import SalesOrderDB
            since = _dt.datetime.utcnow() - _dt.timedelta(days=90)
            created = session.scalars(
                select(SalesOrderDB.created_at)
                .where(SalesOrderDB.store_id == ctx.store_id, SalesOrderDB.created_at >= since)
            ).all()
            for created_at in created:
                local = created_at + _dt.timedelta(hours=ALMATY_UTC_OFFSET_HOURS)
                hourly_grid[local.weekday()][local.hour] += 1
        finally:
            session.close()

    # Активность авто-чата
    chat_activity = _load_chat_activity(ctx.store_id, days=180)

    return {
        "success": True,
        "competitor_leaderboard": leaderboard,
        "catalog_coverage": catalog_coverage,
        "hourly_heatmap": hourly_grid,
        "chat_activity": chat_activity,
    }


@router.get("/abc-xyz")
def get_abc_xyz_analysis(days: int = 90, ctx: StoreContext = Depends(get_store_context)):
    """
    ABC (доля в выручке) / XYZ (стабильность спроса по неделям) анализ —
    считается из sales_orders с 6-часовым TTL-кэшем по магазину и периоду.
    Новых таблиц не требует. XYZ-класс
    честно не проставляется (null), если в периоде меньше 4 полных недель
    истории — иначе коэффициент вариации на короткой выборке вводит в заблуждение.

    XYZ-пороги — терцили CV внутри самого магазина, а не учебные 10%/25%.
    Проверено на живых данных (Бархан, 2026-08-29): классический CV даже
    у топовых по выручке SKU почти всегда >25%, потому что карточки на
    Kaspi живут по кривой «буст в выдаче → пик → спад», а не ровным спросом,
    на который рассчитаны учебные пороги — при 10%/25% абсолютно весь
    каталог схлопывался в один класс Z. Терцили дают X/Y/Z как ранг
    стабильности относительно остального каталога того же магазина.
    """
    days = max(28, min(days, 365))
    cached = _get_cached_abc_xyz(ctx.store_id, days)
    if cached is not None:
        return cached
    session = _get_sync_db_session()
    if not session:
        return {"success": True, "items": [], "matrix": {}, "weeks_covered": 0, "total_revenue": 0.0}
    try:
        from backend.db.models import SalesOrderDB
        since = datetime.utcnow() - timedelta(days=days)
        rows = session.execute(
            select(SalesOrderDB.created_at, SalesOrderDB.entries_payload)
            .where(SalesOrderDB.store_id == ctx.store_id, SalesOrderDB.created_at >= since)
        ).all()

        # Kaspi иногда не отдаёт offer.name/title в entries — kaspi_wrapper.py
        # тогда пишет заглушку "Товар". Подменяем реальным названием из каталога
        # магазина по SKU, если оно у нас есть (см. тот же фикс в _load_sales_summary).
        catalog_names = {
            p.get("sku"): p.get("name")
            for p in _load_products(ctx.store_id)
            if p.get("sku") and p.get("name") and not is_placeholder_name(p.get("name"), p.get("sku"))
        }

        sku_info: dict = {}
        sku_weekly_qty: dict = {}
        week_keys_seen: set = set()

        for r in rows:
            try:
                entries = json.loads(r.entries_payload)
            except Exception:
                entries = []
            week_key = r.created_at.strftime("%G-W%V")
            week_keys_seen.add(week_key)
            for e in entries:
                sku = e.get("sku") or "?"
                name = resolve_product_name(sku, e.get("name"), catalog_names=catalog_names) or sku
                qty = e.get("quantity", 0) or 0
                total_price = e.get("totalPrice", 0) or 0
                info = sku_info.setdefault(sku, {"sku": sku, "name": name, "revenue": 0.0, "quantity": 0})
                info["revenue"] += total_price
                info["quantity"] += qty
                weekly = sku_weekly_qty.setdefault(sku, {})
                weekly[week_key] = weekly.get(week_key, 0) + qty

        weeks_covered = len(week_keys_seen)
        weeks_sorted = sorted(week_keys_seen)
        total_revenue = sum(v["revenue"] for v in sku_info.values())

        # Недели до первой продажи (карточка ещё не выставлена) или после
        # последней (снята с продажи/кончился остаток) — не "нестабильность
        # спроса", а отсутствие спроса как класса. Считаем CV только по
        # активному окну между первой и последней ненулевой неделей.
        MIN_ACTIVE_WEEKS = 4
        cv_by_sku: dict = {}
        for sku, weekly in sku_weekly_qty.items():
            series_full = [weekly.get(wk, 0) for wk in weeks_sorted]
            nonzero_idx = [i for i, v in enumerate(series_full) if v > 0]
            if not nonzero_idx:
                continue
            active = series_full[nonzero_idx[0]:nonzero_idx[-1] + 1]
            if len(active) < MIN_ACTIVE_WEEKS:
                continue
            mean = sum(active) / len(active)
            variance = sum((x - mean) ** 2 for x in active) / len(active)
            cv_by_sku[sku] = (variance ** 0.5) / mean * 100.0

        # Терцильные пороги вместо фиксированных 10%/25% — см. docstring выше.
        # На маленькой выборке терцили сами по себе шумные, поэтому ниже
        # некоторого минимума SKU лучше честно не классифицировать вовсе.
        MIN_SKUS_FOR_TERCILES = 9
        valid_cvs = sorted(cv_by_sku.values())
        if len(valid_cvs) >= MIN_SKUS_FOR_TERCILES:
            cv_x_cutoff = valid_cvs[len(valid_cvs) // 3]
            cv_y_cutoff = valid_cvs[2 * len(valid_cvs) // 3]
        else:
            cv_x_cutoff = cv_y_cutoff = None

        items_sorted = sorted(sku_info.values(), key=lambda x: x["revenue"], reverse=True)
        cumulative = 0.0
        items = []
        for it in items_sorted:
            share = (it["revenue"] / total_revenue * 100.0) if total_revenue > 0 else 0.0
            # Класс по накопленной доле ДО текущего товара: иначе доминирующий
            # SKU (скажем, 85% выручки) сам перешагивает порог и получает B,
            # а класс A остаётся пустым.
            if cumulative < 80.0:
                abc_class = "A"
            elif cumulative < 95.0:
                abc_class = "B"
            else:
                abc_class = "C"
            cumulative += share

            xyz_class = None
            cv_pct = cv_by_sku.get(it["sku"])
            if cv_pct is not None:
                cv_pct = round(cv_pct, 1)
                if cv_x_cutoff is not None:
                    xyz_class = "X" if cv_pct <= cv_x_cutoff else ("Y" if cv_pct <= cv_y_cutoff else "Z")

            items.append({
                "sku": it["sku"],
                "name": it["name"],
                "revenue": round(it["revenue"], 2),
                "revenue_share": round(share, 2),
                "quantity": it["quantity"],
                "abc_class": abc_class,
                "xyz_class": xyz_class,
                "cv_pct": cv_pct,
            })

        matrix = {f"{a}{x}": 0 for a in ("A", "B", "C") for x in ("X", "Y", "Z")}
        for it in items:
            if it["xyz_class"]:
                key = f"{it['abc_class']}{it['xyz_class']}"
                matrix[key] = matrix.get(key, 0) + 1

        result = {
            "success": True,
            "items": items,
            "matrix": matrix,
            "weeks_covered": weeks_covered,
            "total_revenue": round(total_revenue, 2),
        }
        _cache_abc_xyz(ctx.store_id, days, result)
        return result
    finally:
        session.close()


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


@router.get("/score")
def get_score_stats(ctx: StoreContext = Depends(get_store_context)):
    """
    Композитный "score" магазина (в духе Oura Readiness / WHOOP Recovery) +
    streak-счётчики + данные для heatmap выручки. Всё считается из реальных
    таблиц (sales_orders, analytics_daily_history) — если истории ещё нет
    (фича только что задеплоена / стор новый), возвращаем честные нули/пустые
    списки, а не выдуманные тренды.
    """
    import datetime as _dt

    snapshot = _load_analytics_snapshot(ctx.store_id)
    best_price_share = float(snapshot.get("best_price_share", 0.0))

    history = _load_analytics_history(ctx.store_id, days=180)
    daily_180 = _load_sales_daily(ctx.store_id, days=180)
    daily_revenue = {d["date"]: d for d in daily_180}

    # --- Компонент 2: динамика продаж (последние 7 дней vs предыдущие 7) ---
    today = _dt.datetime.utcnow().date()
    last7 = sum(
        daily_revenue.get((today - _dt.timedelta(days=i)).strftime("%Y-%m-%d"), {}).get("revenue", 0.0)
        for i in range(7)
    )
    prev7 = sum(
        daily_revenue.get((today - _dt.timedelta(days=i)).strftime("%Y-%m-%d"), {}).get("revenue", 0.0)
        for i in range(7, 14)
    )
    if prev7 > 0:
        pct_change = (last7 - prev7) / prev7
        revenue_trend_score = _clamp(50 + pct_change * 50)
    else:
        revenue_trend_score = 100.0 if last7 > 0 else 0.0

    # --- Компонент 3: стабильность потока заказов (активных дней из 30) ---
    active_days_30 = sum(
        1 for i in range(30)
        if daily_revenue.get((today - _dt.timedelta(days=i)).strftime("%Y-%m-%d"), {}).get("orders", 0) > 0
    )
    activity_score = _clamp((active_days_30 / 30.0) * 100)

    composite_score = round((best_price_share + revenue_trend_score + activity_score) / 3)

    # --- Streaks ---
    price_leadership_streak = 0
    for i in range(len(history) - 1, -1, -1):
        if history[i]["best_price_share"] >= 50.0:
            price_leadership_streak += 1
        else:
            break

    sales_streak = 0
    for i in range(0, 90):
        day_key = (today - _dt.timedelta(days=i)).strftime("%Y-%m-%d")
        orders_that_day = daily_revenue.get(day_key, {}).get("orders", 0)
        if orders_that_day > 0:
            sales_streak += 1
        else:
            break

    return {
        "success": True,
        "score": {
            "composite": composite_score,
            "components": {
                "price_leadership": round(best_price_share),
                "revenue_trend": round(revenue_trend_score),
                "activity_consistency": round(activity_score),
            },
        },
        "streaks": {
            "price_leadership_days": price_leadership_streak,
            "sales_days": sales_streak,
        },
        "heatmap": daily_180,
        "history_points": len(history),
    }


@router.get("/quality-metrics")
def get_quality_metrics(ctx: StoreContext = Depends(get_store_context)):
    """
    Последний ночной снапшот раздела кабинета Kaspi "Показатели качества"
    (Рейтинг/Возвраты/Задержки при передачах/Отмены по вине/Среднее время
    ответов/Доля ответов) — скрапится раз в сутки ночью
    (backend/services/kaspi_quality_metrics_worker.py). Если воркер ещё ни
    разу не отработал для этого стора (только что подключён), возвращаем
    честный "нет данных", а не выдуманные нули.
    """
    snapshot = _load_quality_metrics_latest(ctx.store_id)
    if not snapshot:
        return {"success": True, "has_data": False, "data": None}
    return {"success": True, "has_data": True, "data": snapshot}


@router.get("/quality-metrics-history")
def get_quality_metrics_history(days: int = 30, ctx: StoreContext = Depends(get_store_context)):
    """Ряд дневных значений rating_percentage за period — для тренда рейтинга
    на дашборде. Не привязка отзывов к рассылке (Kaspi её не даёт, см.
    review_campaign.py), а косвенный график "рейтинг магазина во времени"
    рядом с воронкой рассылки «Отзыв» (/api/chatbot/outbound/stats)."""
    days = max(7, min(days, 180))
    history = _load_quality_metrics_history(ctx.store_id, days=days)
    return {"success": True, "history": history}


# ── Прогноз остатков и закуп ────────────────────────────────────────────
# Параметры срока поставки/периода покрытия приходят из UI и не хранятся на
# бэкенде: у одного магазина разные поставщики с разными сроками, и «одна
# цифра на весь магазин» в настройках была бы враньём. Фронт помнит последний
# выбор локально, выгрузка получает те же значения, что видел продавец на экране.

def _forecast_params(window_days: int, lead_time_days: int, cover_days: int,
                     horizon_days: int, slow_days: int) -> dict:
    return {
        "window_days": max(14, min(window_days, 180)),
        "lead_time_days": max(0, min(lead_time_days, 180)),
        "cover_days": max(1, min(cover_days, 180)),
        "horizon_days": max(1, min(horizon_days, 90)),
        # Ниже срока поставки порог «медленно» терял бы смысл: он бы пересёкся
        # с ведром «не успеет приехать» и забрал бы у него строки.
        "slow_days": max(30, min(slow_days, 720)),
    }



# Дашборд опрашивает эту ручку каждые 5 минут с каждого открытого клиента.
# На каталоге в 5000+ позиций (Q-STEPPE, живые данные) полный ответ весит
# ~3 МБ JSON — тот же класс проблемы, что p95=27.6с на /api/stock-groups
# (HANDOFF §3.52). Урезаем именно items (summary всегда честный по полному
# набору, см. _select_items_for_response) — экспорт в Excel ходит без лимита.
_STOCK_FORECAST_MAX_ITEMS = 500


@router.get("/stock-forecast")
def get_stock_forecast(
    window_days: int = DEFAULT_WINDOW_DAYS,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    cover_days: int = DEFAULT_COVER_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    slow_days: int = DEFAULT_SLOW_DAYS,
    ctx: StoreContext = Depends(get_store_context),
):
    """
    Прогноз «дней до нуля» по каждой позиции и рекомендованный объём закупа.

    Скорость продаж — из sales_orders (реальные завершённые заказы), остаток —
    из синхронизированного каталога, мастер-группы схлопнуты в одну строку на
    общий остаток. Подробности расчёта и его сознательные ограничения —
    backend/services/stock_forecast.py. Пусто, пока sales_sync_worker не
    сделал первый проход по стору.

    Ответ на большом каталоге урезан до _STOCK_FORECAST_MAX_ITEMS строк
    (summary при этом честный по полному набору) — см. items_truncated и
    /purchase-order-export для полного списка.
    """
    params = _forecast_params(window_days, lead_time_days, cover_days, horizon_days, slow_days)
    return {
        "success": True,
        **compute_stock_forecast(ctx.store_id, max_items=_STOCK_FORECAST_MAX_ITEMS, **params),
    }


@router.get("/purchase-order-export")
def export_purchase_order(
    window_days: int = DEFAULT_WINDOW_DAYS,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    cover_days: int = DEFAULT_COVER_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    slow_days: int = DEFAULT_SLOW_DAYS,
    only_reorder: bool = True,
    ctx: StoreContext = Depends(get_store_context),
):
    """Заявка поставщику в Excel по тому же прогнозу, что показан на экране."""
    params = _forecast_params(window_days, lead_time_days, cover_days, horizon_days, slow_days)
    forecast = compute_stock_forecast(ctx.store_id, **params)
    xlsx_bytes = build_purchase_order_xlsx(forecast, only_reorder=only_reorder)
    filename = f"purchase_order_{_date.today().isoformat()}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
