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
        merchant_id = "20186986"
        logger.warning(f"Не удалось получить динамический merchant_id. Используем fallback: {merchant_id}")

    from backend.state_helpers import _load_master_stock_groups
    master_groups = _load_master_stock_groups(store_id=store_id)
    effective_configs = dict(configs)
    
    # Обогащаем конфигурации демпинга из настроек Мастер-Групп
    for gid, group in master_groups.items():
        if not group.get("is_active", True):
            continue
        g_dump_cfg = group.get("dumping_config")
        if g_dump_cfg and g_dump_cfg.get("enabled"):
            for sku in group.get("linked_skus", []):
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

    # Сохраняем агрегированную аналитику в стейт
    try:
        import os, json
        os.makedirs("states", exist_ok=True)
        with open("states/analytics.json", "w", encoding="utf-8") as f:
            json.dump(analytics, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # Бродкаст аналитики в WebSocket
    from backend.dumper.telemetry import telemetry_manager
    await telemetry_manager.broadcast("analytics_update", analytics)

    # 3. 🚀 Воркер-Исполнитель
    exec_result = RateLimitExecutorWorker.execute_updates(update_queue, save_override_func, merchant_id=merchant_id)
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
