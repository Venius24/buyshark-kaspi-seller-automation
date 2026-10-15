"""Поиск по данным текущего магазина."""

import asyncio

from backend.assistant.rag.index import get_store_index
from backend.assistant.tools import ToolContext, tool

_TYPES = {"product", "card", "sales_day", "price_war", "dumper_log"}
_TYPE_ACCESS = {"product": "products", "card": "products", "sales_day": "overview",
                "price_war": "dumping", "dumper_log": "dumping"}
_MAX_K = 10
_METADATA_KEYS = ("merchantSku", "kaspiSku", "brand", "category", "status", "stock", "price", "has_image")


def _result(hit) -> dict:
    metadata = hit.document.metadata
    fields = {key: value[:100] if isinstance(value, str) else value
              for key in _METADATA_KEYS if (value := metadata.get(key)) is not None}
    return {"source": hit.source, "score": round(hit.score, 4),
            "text": hit.document.text[:700 if hit.document.doc_type == "card" else 400], "metadata": fields,
            **({"url": str(metadata["url"])[:500]} if metadata.get("url") else {})}


@tool(
    name="search_store_knowledge",
    description="Локальный поиск RU/EN по товарам, дневным продажам, радару и логам демпинга магазина.",
    parameters={"type": "object", "properties": {
        "query": {"type": "string"}, "k": {"type": "integer", "default": 5, "minimum": 1, "maximum": _MAX_K},
        "doc_types": {"type": "array", "items": {"type": "string", "enum": sorted(_TYPES)}},
        "sku": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
    section="assistant",
)
async def search_store_knowledge(args: dict, ctx: ToolContext) -> dict:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        return {"error": "query должен быть непустой строкой до 500 символов"}
    raw_k = args.get("k", 5)
    try:
        k = int(raw_k)
    except (TypeError, ValueError):
        return {"error": "k должен быть целым числом от 1 до 10"}
    if isinstance(raw_k, (bool, float)) or not 1 <= k <= _MAX_K:
        return {"error": "k должен быть целым числом от 1 до 10"}
    requested = args.get("doc_types", sorted(_TYPES))
    if not isinstance(requested, list) or not requested or any(t not in _TYPES for t in requested):
        return {"error": "doc_types должен содержать типы product, card, sales_day, price_war, dumper_log"}
    sku = args.get("sku")
    if sku is not None and (not isinstance(sku, str) or not sku.strip() or len(sku) > 100):
        return {"error": "sku должен быть непустой строкой до 100 символов"}
    allowed = [t for t in requested if ctx.sections is None or _TYPE_ACCESS[t] in ctx.sections]
    if not allowed:
        return {"results": [], "note": "Нет доступа к запрошенным источникам"}
    index = await get_store_index(ctx.store_id)
    hits = await asyncio.to_thread(index.search, query, k, allowed, sku)
    return {"results": [_result(hit) for hit in hits],
            **({"note": "Совпадений в данных магазина нет"} if not hits else {})}
