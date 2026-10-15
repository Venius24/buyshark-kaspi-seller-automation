"""Компактный отчёт из уже существующих расчётов кабинета."""

import asyncio
import logging

from backend.assistant.builtin_tools import _annotated_catalog, _no_token
from backend.assistant.tools import ToolContext, tool

logger = logging.getLogger(__name__)
_SECTIONS = ("sales", "finance", "stock", "pricing", "problems")
_ACCESS = {"sales": "overview", "finance": "overview", "stock": "products",
           "pricing": "dumping", "problems": "dumping"}
_MAX_ROWS = 10


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason[:200]}


async def _section(name: str, ctx: ToolContext, days: int) -> dict:
    if ctx.sections is not None and _ACCESS[name] not in ctx.sections:
        return _unavailable(f"Раздел '{_ACCESS[name]}' не открыт")
    if name == "sales":
        from backend import state_helpers
        raw = await asyncio.to_thread(state_helpers._load_sales_summary, ctx.store_id, days=days)
        if not raw or (not raw.get("daily") and not raw.get("total_orders")):
            return _unavailable("Нет данных продаж за период")
        return {"status": "ok", "total_revenue": raw.get("total_revenue"),
                "total_orders": raw.get("total_orders"), "avg_order_value": raw.get("avg_order_value"),
                "top_skus": [{"sku": str(row.get("sku") or "")[:80], "name": str(row.get("name") or "")[:120],
                              "revenue": row.get("revenue"), "quantity": row.get("quantity")}
                             for row in (raw.get("top_skus") or [])[:_MAX_ROWS]]}
    if name == "finance":
        if not ctx.token:
            return _unavailable(_no_token()["message"])
        from backend.services import order_finance
        raw = await asyncio.to_thread(order_finance.compute_store_finance, ctx.token, days)
        commission = raw.get("commission") or {}
        revenue = float(raw.get("revenue") or 0)
        net = float(raw.get("net_after_kaspi") or 0)
        return {"status": "ok", "period": raw.get("period"), "orders": raw.get("orders"),
                "revenue": raw.get("revenue"), "commission": {key: commission.get(key) for key in
                ("total", "share_of_revenue_pct", "is_estimate", "uncertain_orders")},
                "net_after_kaspi": raw.get("net_after_kaspi"),
                "net_after_kaspi_pct": round(net / revenue * 100, 2) if revenue else 0.0,
                "note": "После комиссий и доставки Kaspi; себестоимость и налоги не учтены"}
    if name == "stock":
        from backend.services import stock_forecast
        raw = await asyncio.to_thread(stock_forecast.compute_stock_forecast, ctx.store_id,
                                      window_days=days, max_items=_MAX_ROWS)
        return {"status": "ok", "summary": {key: (raw.get("summary") or {}).get(key) for key in
                ("total_items", "reorder_positions", "reorder_units", "reorder_cost", "frozen_cost")},
                "items_shown": min(len(raw.get("items") or []), _MAX_ROWS),
                "items": [{"merchant_sku": str(item.get("merchant_sku") or "")[:80],
                           "name": str(item.get("name") or "")[:120],
                           **{key: item.get(key) for key in ("stock", "sold", "bucket", "recommended_qty")}}
                          for item in (raw.get("items") or [])[:_MAX_ROWS]]}

    products = await _annotated_catalog(ctx.store_id, only_active=True)
    if products is None:
        return _unavailable("Каталог ещё не синхронизирован")
    from backend.services import dumping_filters
    if name == "pricing":
        facets = dumping_filters.facet_counts(products)
        below = [p for p in products if p.get("dumping_config") and p.get("price_num") is not None
                 and p.get("dumping_config", {}).get("min_price") is not None
                 and float(p["price_num"]) < float(p["dumping_config"]["min_price"])]
        losing = [p for p in products if p.get("rank_state") == dumping_filters.RANK_STATE_NOT_LEADER]
        return {"status": "ok", "total_active": facets.get("TOTAL"),
                "below_minimum_count": len(below), "losing_count": len(losing),
                "below_minimum": [{"sku": p.get("merchantSku"), "price": p.get("price_num"),
                                   "min_price": p["dumping_config"].get("min_price")} for p in below[:_MAX_ROWS]],
                "losing": [{"sku": p.get("merchantSku"), "rank": (p.get("price_war") or {}).get("our_rank")}
                           for p in losing[:_MAX_ROWS]]}
    # Продажи по SKU из того же сводного загрузчика; нулевой остаток считаем
    # проблемой лишь при положительных продажах в выбранном окне.
    from backend import state_helpers
    sales = await asyncio.to_thread(state_helpers._load_sales_summary, ctx.store_id, days=days)
    sold = {str(row.get("sku")): row for row in (sales or {}).get("top_skus") or []}
    zero = [p for p in products if p.get("stock") is not None and float(p["stock"]) == 0
            and float((sold.get(str(p.get("merchantSku"))) or {}).get("quantity") or 0) > 0]
    losing = [p for p in products if p.get("rank_state") == dumping_filters.RANK_STATE_NOT_LEADER]
    below = [p for p in products if p.get("dumping_config") and p.get("price_num") is not None
             and p.get("dumping_config", {}).get("min_price") is not None
             and float(p["price_num"]) < float(p["dumping_config"]["min_price"])]
    return {"status": "ok", "zero_stock_with_sales_count": len(zero), "losing_count": len(losing),
            "zero_stock_with_sales_scope": "top_skus_only",
            "zero_stock_with_sales_note": "Проверены только топ SKU по продажам из сводки, не весь каталог",
            "below_minimum_count": len(below),
            "below_minimum": [{"sku": p.get("merchantSku"), "price": p.get("price_num"),
                               "min_price": p["dumping_config"].get("min_price")} for p in below[:_MAX_ROWS]],
            "zero_stock_with_sales": [{"sku": p.get("merchantSku"),
                                       "sold": sold[str(p.get("merchantSku"))].get("quantity")}
                                      for p in zero[:_MAX_ROWS]],
            "losing": [{"sku": p.get("merchantSku"), "rank": (p.get("price_war") or {}).get("our_rank")}
                       for p in losing[:_MAX_ROWS]]}


@tool(
    name="build_report",
    description="Отчёт по разделам sales, finance, stock, pricing, problems за последние 1–90 дней.",
    parameters={"type": "object", "properties": {
        "sections": {"type": "array", "items": {"type": "string", "enum": list(_SECTIONS)},
                     "description": "По умолчанию sales и problems"},
        "period_days": {"type": "integer", "default": 30, "minimum": 1, "maximum": 90}},
        "additionalProperties": False},
    section="overview",
)
async def build_report(args: dict, ctx: ToolContext) -> dict:
    selected = args.get("sections", ["sales", "problems"])
    if not isinstance(selected, list) or not selected or any(s not in _SECTIONS for s in selected):
        return {"error": f"sections должен быть непустым подмножеством: {', '.join(_SECTIONS)}"}
    raw_days = args.get("period_days", 30)
    try:
        days = int(raw_days)
    except (TypeError, ValueError):
        return {"error": "period_days должен быть целым числом от 1 до 90"}
    if isinstance(raw_days, (bool, float)) or not 1 <= days <= 90:
        return {"error": "period_days должен быть целым числом от 1 до 90"}
    result = {"store_id": ctx.store_id, "period_days": days, "sections": {}}
    for name in dict.fromkeys(selected):
        try:
            result["sections"][name] = await _section(name, ctx, days)
        except Exception as exc:
            logger.warning("Ассистент: раздел отчёта %s недоступен для %s: %s", name, ctx.store_id, exc)
            result["sections"][name] = _unavailable("Не удалось загрузить данные раздела")
    return result
