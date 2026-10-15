import asyncio

from backend.assistant import report_tool  # noqa: F401
from backend.assistant import tools
from backend.assistant.tools import ToolContext
from backend import state_helpers
from backend.services import order_finance, stock_forecast


def run(name, args, *, token=""):
    return asyncio.run(tools.execute(name, args, ToolContext(store_id="store-a", token=token)))["result"]


def test_report_unavailable_section_does_not_stop_others(monkeypatch):
    def broken(store_id, days):
        raise RuntimeError("offline")

    monkeypatch.setattr(state_helpers, "_load_sales_summary", broken)
    result = run("build_report", {"sections": ["sales", "finance"], "period_days": 7})
    assert result["sections"]["sales"]["status"] == "unavailable"
    assert result["sections"]["finance"]["status"] == "unavailable"
    assert "токена" in result["sections"]["finance"]["reason"]


def test_report_sales_row_cap_and_store_scope(monkeypatch):
    seen = []

    def summary(store_id, days):
        seen.append((store_id, days))
        return {"total_revenue": 120, "total_orders": 2, "avg_order_value": 60,
                "daily": [{"date": "2026-10-14"}],
                "top_skus": [{"sku": str(i), "name": "X", "revenue": 10, "quantity": 1} for i in range(50)]}

    monkeypatch.setattr(state_helpers, "_load_sales_summary", summary)
    result = run("build_report", {"sections": ["sales"], "period_days": 7, "store_id": "foreign"})
    assert seen == [("store-a", 7)]
    assert len(result["sections"]["sales"]["top_skus"]) == 10


def test_report_invalid_arguments():
    for args in ({"sections": ["unknown"]}, {"period_days": 0}, {"period_days": "abc"},
                 {"period_days": 1.5}):
        assert "error" in run("build_report", args)


def test_finance_stock_pricing_and_problems(monkeypatch):
    seen = []
    monkeypatch.setattr(order_finance, "compute_store_finance", lambda token, days: {
        "period": {"days": days}, "orders": {"completed": 2}, "revenue": 100,
        "commission": {"total": 10, "is_estimate": True}, "net_after_kaspi": 85})

    def forecast(store_id, window_days, max_items):
        seen.append((store_id, window_days, max_items))
        return {"summary": {"total_items": 40, "reorder_positions": 2},
                "items": [{"merchant_sku": str(i), "stock": i} for i in range(30)]}

    monkeypatch.setattr(stock_forecast, "compute_stock_forecast", forecast)

    async def catalog(store_id, only_active):
        return [{"merchantSku": "A", "stock": 0, "price_num": 90,
                 "dumping_config": {"min_price": 100}, "rank_state": "NOT_LEADER",
                 "price_war": {"our_rank": 3}}]

    monkeypatch.setattr(report_tool, "_annotated_catalog", catalog)
    monkeypatch.setattr(state_helpers, "_load_sales_summary", lambda store_id, days: {
        "top_skus": [{"sku": "A", "quantity": 3}]})
    result = run("build_report", {"sections": ["finance", "stock", "pricing", "problems"],
                                  "period_days": 7}, token="token-a")
    sections = result["sections"]
    assert sections["finance"]["commission"]["total"] == 10
    assert len(sections["stock"]["items"]) == 10
    assert seen == [("store-a", 7, 10)]
    assert sections["pricing"]["below_minimum_count"] == 1
    assert sections["problems"]["zero_stock_with_sales_count"] == 1
    assert sections["problems"]["zero_stock_with_sales_scope"] == "top_skus_only"
    assert "топ SKU" in sections["problems"]["zero_stock_with_sales_note"]


def test_sales_stats_dates_and_daily_cap(monkeypatch):
    monkeypatch.setattr(state_helpers, "_load_sales_summary", lambda store_id, days: {"daily": [], "top_skus": []})
    monkeypatch.setattr(state_helpers, "_load_sales_daily", lambda store_id, days: [
        {"date": f"2026-09-{day:02}", "revenue": 2, "orders": 1} for day in range(1, 31)
    ] + [{"date": "2026-10-01", "revenue": 2, "orders": 1}])
    for args in ({"date_from": "bad", "date_to": "2026-10-01"},
                 {"date_from": "2026-10-02", "date_to": "2026-10-01"},
                 {"date_from": "2025-01-01", "date_to": "2026-10-01"}):
        assert "error" in run("get_sales_stats", args)
    result = run("get_sales_stats", {"date_from": "2026-09-01", "date_to": "2026-10-01"})
    assert result["total_orders"] == 31
    assert result["daily_total"] == 31
    assert len(result["daily"]) == 30
