"""Nightly collector for seller quality metrics — WITHHELD in the public version
(see kaspi_quality_metrics.py). The loop is a no-op here."""
import logging

logger = logging.getLogger(__name__)


async def run_quality_metrics_cycle():
    return None


async def start_quality_metrics_loop():
    logger.info("[QualityMetrics] withheld in the public version, loop disabled")
