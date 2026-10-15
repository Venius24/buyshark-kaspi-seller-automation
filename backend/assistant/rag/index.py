"""Store-scoped TTL кэш BM25 индексов."""

import asyncio
import logging
import threading
import time

from backend.assistant.config import rag_ttl_seconds
from backend.assistant.rag.bm25 import BM25Retriever
from backend.assistant.rag.documents import build_documents

logger = logging.getLogger(__name__)
_CACHE: dict[str, tuple[float, BM25Retriever]] = {}
_LOCK = threading.Lock()
_MAX_LOGS = 100


async def get_store_index(store_id: str) -> BM25Retriever:
    with _LOCK:
        entry = _CACHE.get(store_id)
        if entry and entry[0] > time.monotonic():
            return entry[1]

    from backend.assistant.builtin_tools import _annotated_catalog
    from backend.autolist.validator import list_drafts
    from backend import state_helpers

    # Каждый загрузчик уже применяет store_id в запросе. Ошибка одного
    # источника не скрывает документы других источников.
    try:
        products = await _annotated_catalog(store_id, only_active=False) or []
    except Exception as exc:
        logger.warning("RAG products for %s: %s", store_id, exc)
        products = []

    async def optional(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, store_id, *args, **kwargs) or []
        except Exception as exc:
            logger.warning("RAG source %s for %s: %s", fn.__name__, store_id, exc)
            return []

    daily, war, logs, approved, submitted = await asyncio.gather(
        optional(state_helpers._load_sales_daily, days=90),
        optional(state_helpers._load_price_war_snapshot),
        optional(state_helpers._load_dumper_logs, limit=_MAX_LOGS),
        optional(list_drafts, status="approved"),
        optional(list_drafts, status="submitted"),
    )
    index = await asyncio.to_thread(lambda: BM25Retriever(
        build_documents(products, daily, war, logs, approved + submitted)))
    with _LOCK:
        _CACHE[store_id] = (time.monotonic() + rag_ttl_seconds(), index)
    return index


def clear_cache() -> None:
    """Тестовая очистка; не влияет на источники данных."""
    with _LOCK:
        _CACHE.clear()
