"""Market radar — WITHHELD in the public version.

The private version collects competitor offers for every active Master SKU from
Kaspi's public product pages, in a bounded crawl pool, and raises a Telegram alert
when several full cycles in a row come back empty ("blind radar") instead of letting
the dumper act on a silently empty market. Its transport layer was built to keep
working against Kaspi's bot protection, which is why it is not published.

This stub keeps the interface: the market snapshot is always empty, so the dumper
never moves a price in the public version.
"""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

RADAR_CRAWL_WORKERS = 0


def _fetch_offers_sync(master_sku: str, city_code: str) -> List[dict]:
    return []


async def fetch_competitor_offers(master_sku: str, city_code: str) -> List[dict]:
    return []


class RadarWorker:
    """Builds the market snapshot {master_sku: {...}} the dumper's brain consumes."""

    @staticmethod
    async def scan_market(products: List[dict], configs: dict, store_id=None) -> Dict[str, dict]:
        logger.info("[Radar] withheld in the public version, empty market snapshot")
        return {}
