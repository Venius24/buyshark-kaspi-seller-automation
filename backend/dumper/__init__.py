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
    save_override_func
) -> dict:
    """
    Оркестратор трех воркеров (Radar -> Brain -> Executor).
    Сохраняет 100% обратную совместимость с API бэкенда.
    """
    merchant_id = discover_merchant_id(token)
    if not merchant_id:
        merchant_id = "20186986"
        logger.warning(f"Не удалось получить динамический merchant_id. Используем fallback: {merchant_id}")

    # 1. 📡 Воркер-Радар
    market_snapshot = await RadarWorker.scan_market(products, configs)
    if not market_snapshot:
        return {
            "success": True,
            "updated_count": 0,
            "message": "Нет товаров с включенным авто-демпингом.",
            "logs": []
        }

    # 2. 🧠 Воркер-Мозг
    update_queue = BrainWorker.calculate_price_decisions(market_snapshot, configs, merchant_id)

    # 3. 🚀 Воркер-Исполнитель
    return RateLimitExecutorWorker.execute_updates(update_queue, save_override_func)

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
