"""
Фасад-адаптер для 100% обратной совместимости импортов backend.price_dumper.
Вся логика перенесена в модульный пакет backend.dumper (config, radar, brain, executor, xml_feed).
"""

from backend.dumper import (
    discover_merchant_id,
    _decode_base64_if_needed,
    DumpingProductConfig,
    RadarWorker,
    fetch_competitor_offers,
    _fetch_offers_sync,
    BrainWorker,
    DumpingEngine,
    RateLimitExecutorWorker,
    generate_xml_feed,
    run_price_dumping_job,
)

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
    "run_price_dumping_job",
]
