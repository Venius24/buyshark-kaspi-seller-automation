import asyncio

from backend.assistant import rag_tool  # noqa: F401
from backend.assistant import tools
from backend.assistant.rag.index import clear_cache
from backend.assistant.tools import ToolContext
from backend import state_helpers
from backend.assistant import builtin_tools


def run(store_id, args):
    return asyncio.run(tools.execute("search_store_knowledge", args, ToolContext(store_id=store_id)))["result"]


def test_store_isolation_and_russian_search(monkeypatch):
    clear_cache()

    async def catalog(store_id, only_active):
        return [{"merchantSku": store_id, "name": "Синяя ваза" if store_id == "a" else "Красная лампа",
                 "brand": "B", "stock": 1, "price_num": 100}]

    monkeypatch.setattr(builtin_tools, "_annotated_catalog", catalog)
    monkeypatch.setattr(state_helpers, "_load_sales_daily", lambda store_id, days: [])
    monkeypatch.setattr(state_helpers, "_load_price_war_snapshot", lambda store_id: [])
    monkeypatch.setattr(state_helpers, "_load_dumper_logs", lambda store_id, limit: [])
    a = run("a", {"query": "синяя"})
    b = run("b", {"query": "синяя"})
    assert [r["source"]["sku"] for r in a["results"]] == ["a"]
    assert b == {"results": [], "note": "Совпадений в данных магазина нет"}


def test_rag_invalid_arguments_and_cap(monkeypatch):
    clear_cache()

    async def catalog(store_id, only_active):
        return [{"merchantSku": str(i), "name": "Ваза", "stock": 1, "price_num": 2} for i in range(30)]

    monkeypatch.setattr(builtin_tools, "_annotated_catalog", catalog)
    monkeypatch.setattr(state_helpers, "_load_sales_daily", lambda store_id, days: [])
    monkeypatch.setattr(state_helpers, "_load_price_war_snapshot", lambda store_id: [])
    monkeypatch.setattr(state_helpers, "_load_dumper_logs", lambda store_id, limit: [])
    for args in ({"query": ""}, {"query": "ваза", "k": 0}, {"query": "ваза", "k": 1.5},
                 {"query": "ваза", "doc_types": ["bad"]}):
        assert "error" in run("a", args)
    assert len(run("a", {"query": "ваза", "k": 10})["results"]) == 10
    assert [r["source"]["sku"] for r in run("a", {"query": "ваза", "sku": "3"})["results"]] == ["3"]
