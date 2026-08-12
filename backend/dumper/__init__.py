import asyncio
import logging
from typing import List
from backend.dumper.config import discover_merchant_id, _decode_base64_if_needed, DumpingProductConfig
from backend.dumper.radar import RadarWorker, fetch_competitor_offers, _fetch_offers_sync
from backend.dumper.brain import BrainWorker, DumpingEngine
from backend.dumper.executor import RateLimitExecutorWorker
from backend.dumper.xml_feed import generate_xml_feed

logger = logging.getLogger(__name__)

async def run_price_dumping_job(
    token: str,
    configs: dict,
    products: List[dict],
    save_override_func,
    store_id=None
) -> dict:
    """
    Оркестратор трех воркеров (Radar -> Brain -> Executor).
    Сохраняет 100% обратную совместимость с API бэкенда.

    store_id=None (по умолчанию) — легаси-поведение, нескоуп-чтение мастер-групп
    остатка (как было всегда). store_id задан (Phase 7 per-store вызовы) —
    мастер-группы остатка фильтруются по стору, чтобы не подмешивать чужие.
    """
    merchant_id = discover_merchant_id(token)
    if not merchant_id:
        # НЕ подставляем чужой merchant_id (раньше был хардкод "20186986" —
        # реальный ID Barkhan, из-за чего демпинг для любого другого магазина
        # с недоступным /pointofservices тихо работал под его личностью и
        # ловил 403 от Kaspi на каждом шаге). Честно пропускаем цикл для
        # этого стора — следующий тик может определить merchant_id успешно.
        logger.warning("Не удалось определить merchant_id — пропускаем цикл демпинга для этого магазина.")
        return {"success": False, "message": "Не удалось определить Merchant ID магазина (Kaspi API недоступен). Попробуйте позже.", "logs": []}

    from backend.state_helpers import _load_master_stock_groups
    master_groups = _load_master_stock_groups(store_id=store_id)
    effective_configs = dict(configs)
    
    # Обогащаем конфигурации демпинга из настроек Мастер-Групп
    for gid, group in master_groups.items():
        if not group.get("is_active", True):
            continue
        g_dump_cfg = group.get("dumping_config")
        excluded_skus = set(group.get("dumping_excluded_skus") or [])
        if g_dump_cfg and g_dump_cfg.get("enabled"):
            for sku in group.get("linked_skus", []):
                if sku in excluded_skus:
                    # Товар явно исключён из группового демпинга — групповой
                    # конфиг его не касается, но собственный individual
                    # конфиг (если задан) продолжает работать как обычно.
                    continue
                indiv_cfg = effective_configs.get(sku)
                # Если индивидуального конфига нет или он выключен - применяем демпинг группы
                if not indiv_cfg or not indiv_cfg.get("enabled"):
                    merged = dict(g_dump_cfg)
                    merged["sku"] = sku
                    effective_configs[sku] = merged

    # 1. 📡 Воркер-Радар
    market_snapshot = await RadarWorker.scan_market(products, effective_configs)
    if not market_snapshot:
        return {
            "success": True,
            "updated_count": 0,
            "message": "Нет товаров с включенным авто-демпингом.",
            "logs": []
        }

    # 2. 🧠 Воркер-Мозг
    brain_result = BrainWorker.calculate_price_decisions(market_snapshot, effective_configs, merchant_id)
    update_queue = brain_result["update_queue"]
    analytics = brain_result["analytics"]
    calculation_logs = brain_result.get("calculation_logs", [])

    # Сохраняем агрегированную аналитику в стейт (per-store, Слайс 5)
    try:
        from backend.state_helpers import _save_analytics_snapshot, _upsert_analytics_daily_history
        _save_analytics_snapshot(analytics, store_id=store_id)
        _upsert_analytics_daily_history(analytics, store_id=store_id)
    except Exception:
        pass

    # Piggyback: расклад демпинг-войны по каждому SKU + кто нас обходит —
    # из уже загруженных в этом цикле offers, без лишних запросов к Kaspi.
    try:
        from backend.state_helpers import _upsert_price_war_snapshot, _append_competitor_encounters
        _upsert_price_war_snapshot(brain_result.get("price_war_items", []), store_id=store_id)
        _append_competitor_encounters(brain_result.get("competitor_encounters", []), store_id=store_id)
    except Exception:
        pass

    # Бродкаст аналитики в WebSocket
    from backend.dumper.telemetry import telemetry_manager
    await telemetry_manager.broadcast("analytics_update", analytics, store_id=store_id)

    # 3. 🚀 Воркер-Исполнитель
    # execute_updates — синхронная функция с последовательными сетевыми
    # запросами к Kaspi MC и time.sleep() джиттером/backoff'ом между ними
    # (до 60с при 429/403). Вызов без to_thread замораживал единственный
    # event loop приложения (--workers 1) на всё время выполнения очереди —
    # это и был периодический "сервис недоступен" каждые ~5 минут.
    exec_result = await asyncio.to_thread(
        RateLimitExecutorWorker.execute_updates,
        update_queue, save_override_func, merchant_id=merchant_id, store_id=store_id,
    )
    exec_result["analytics"] = analytics
    
    # Объединяем диагностические логи расчета и выполнения
    all_logs = list(calculation_logs) + list(exec_result.get("logs", []))
    exec_result["logs"] = all_logs
    return exec_result

__all__ = [
    "discover_merchant_id",
    "_decode_base64_if_needed",
    "DumpingProductConfig",
    "RadarWorker",
    "fetch_competitor_offers",
    "_fetch_offers_sync",
    "BrainWorker",
    "DumpingEngine",
    "RateLimitExecutorWorker",
    "generate_xml_feed",
    "run_price_dumping_job"
]
