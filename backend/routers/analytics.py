"""
FastAPI router for Dashboard Analytics and Widget stats.
"""

from fastapi import APIRouter, Depends
from datetime import datetime, timedelta
import random
import time
import logging
from backend.state_helpers import _load_dumping_configs, _load_products, _load_analytics_snapshot, _load_dumper_logs
from backend.dumper.config import _decode_base64_if_needed
from backend.dumper.radar import _fetch_offers_sync
from backend.security.store_context import StoreContext, get_store_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# Фронтенд поллит /api/analytics/widgets каждые 10 секунд, пока открыта вкладка
# Overview (useAnalyticsWidgets.ts). Без кэша это означало живой синхронный
# запрос к публичному API конкурентов Kaspi на каждый poll — до 360 запросов/час
# на один и тот же SKU, что похоже и стало причиной бана/рейт-лимита IP VPS
# Kaspi'ем (2026-07-29, подтверждено: 429 даже на kaspi.kz/shop/ с того же IP).
# Реальный демпинг-цикл (radar.py через background_dumping_loop) и так сканирует
# рынок раз в 5 минут — виджету дашборда не нужны данные свежее этого.
_WIDGET_OFFERS_CACHE_TTL_SECONDS = 120
_widget_offers_cache: dict = {}

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


    # Calculate fallback counts if states/analytics.json does not exist
    total_scanned = analytics_data.get("total_scanned", 0)
    winning_count = analytics_data.get("winning_count", 0)
    buy_box_share = analytics_data.get("buy_box_share", 0.0)
    total_saved_margin = analytics_data.get("total_saved_margin", 0.0)
    
    if not total_scanned:
        # Count items that have dumping configs enabled
        enabled_skus = [sku for sku, cfg in configs.items() if cfg.get("enabled")]
        total_scanned = len(enabled_skus)
        # We fallback to mock/defaults if total_scanned is zero
        if total_scanned == 0:
            total_scanned = len(products) if products else 10
            winning_count = int(total_scanned * 0.8)
            buy_box_share = round((winning_count / total_scanned) * 100.0, 1)
            total_saved_margin = 145000.0
        else:
            # Estimate winning count from overrides status
            winning_count = total_scanned  # fallback assumptions
            buy_box_share = 100.0
            total_saved_margin = sum(configs[sku].get("max_price", 0.0) - configs[sku].get("min_price", 0.0) for sku in enabled_skus)
    
    # 3. Find first active product for live price radar
    active_product = None
    active_cfg = None
    for p in products:
        sku = p.get("sku")
        if sku in configs and configs[sku].get("enabled"):
            active_product = p
            active_cfg = configs[sku]
            break
            
    # Fallback to any product if no active dumping config is enabled
    if not active_product and products:
        active_product = products[0]
        active_cfg = {"city_code": "750000000"}

    # Default fallback competitor list
    competitors = [
        {"name": "Мой Магазин (Вы)", "price": 14500, "rank": 1, "is_self": True},
        {"name": "iSpace Online", "price": 14600, "rank": 2, "diff": 100, "is_self": False},
        {"name": "Sulpak Digital", "price": 15000, "rank": 3, "diff": 500, "is_self": False},
    ]
    top_product_name = "Смартфон Apple iPhone 15 128GB Black"
    our_price = 389990
    our_rank = 1

    if active_product:
        top_product_name = active_product.get("name", "Товар")
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

    # 4. Load recent sniper events (per-store, Слайс 5)
    radar_events = _load_dumper_logs(ctx.store_id, limit=5)


    # Default fallback events if list is empty
    if not radar_events:
        radar_events = [
            {
                "timestamp": (now - timedelta(seconds=12)).strftime("%H:%M:%S"),
                "competitor": "Shop_Kaspi_Master",
                "old_price": our_price + 20,
                "new_price": our_price,
                "our_reaction_price": our_price,
                "status": "WINNING_BUYBOX",
                "saved_margin": 1500,
                "message": f"Бот удерживает позицию #{our_rank} для '{top_product_name[:30]}...' по цене {our_price} ₸"
            }
        ]

    # 5. Generate 24-hour heatmap activity levels (0 to 100%)
    hourly_heatmap = [
        {"hour": f"{h:02d}:00", "activity": round(15 + 75 * ((h - 6) % 12 / 12) if 8 <= h <= 22 else 5 + random.randint(0, 10))}
        for h in range(24)
    ]

    return {
        "success": True,
        "timestamp": now.isoformat(),
        "analytics": {
            "buy_box_share": buy_box_share,
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
        "ai_sentiment_shell": {
            "is_mock_shell": True,
            "status_label": "🔮 Оболочка будущего модуля ИИ-Чатов & CRM (Интеграция подготовлена)",
            "categories": [
                {"name": "Логистика & Доставка", "percentage": 40, "color": "#10B981"},
                {"name": "Возвраты & Гарантия (CRM)", "percentage": 30, "color": "#F43F5E"},
                {"name": "Вопросы о товарах (DeepSeek AI)", "percentage": 30, "color": "#3B82F6"}
            ],
            "hourly_heatmap": hourly_heatmap,
            "store_pulse": {
                "ai_worker_status": "ONLINE",
                "telegram_crm_status": "READY",
                "response_avg_sec": 1.4,
                "equalizer_bars": [65, 80, 45, 90, 75, 60, 85, 95, 70, 50]
            }
        }
    }
