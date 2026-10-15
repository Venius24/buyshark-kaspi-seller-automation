import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import state_helpers
from backend.assistant import builtin_tools, rag_tool, tools  # noqa: F401 — rag_tool регистрирует tool
from backend.assistant.rag.index import clear_cache
from backend.assistant.rag.documents import build_documents
from backend.assistant.tools import ToolContext
from backend.autolist import validator
from backend.db.models import DraftProductDB


def search(store_id, query, **kwargs):
    return asyncio.run(tools.execute(
        "search_store_knowledge", {"query": query, **kwargs}, ToolContext(store_id=store_id)))["result"]


def sources(monkeypatch, products, drafts):
    clear_cache()

    async def catalog(store_id, only_active):
        return products.get(store_id, [])

    monkeypatch.setattr(builtin_tools, "_annotated_catalog", catalog)
    monkeypatch.setattr(state_helpers, "_load_sales_daily", lambda store_id, days: [])
    monkeypatch.setattr(state_helpers, "_load_price_war_snapshot", lambda store_id: [])
    monkeypatch.setattr(state_helpers, "_load_dumper_logs", lambda store_id, limit: [])
    monkeypatch.setattr(validator, "list_drafts", drafts)


def test_product_fields_and_identifier_search(monkeypatch):
    url = "https://kaspi.kz/shop/p/sinyaya-vaza-958630914/?c=750000000"
    product = {"merchantSku": "VAZA-42", "kaspiSku": "958630914", "name": "Синяя ваза",
               "url": url, "brand": "Марка", "status": "Активен", "stock": 7,
               "price_num": 12500, "image": "https://example.test/image.jpg"}
    sources(monkeypatch, {"a": [product]}, lambda store_id, status: [])
    for query in ("VAZA-42", "958630914", "sinyaya-vaza", "kaspi.kz/shop/p/sinyaya-vaza-958630914"):
        rows = search("a", query, doc_types=["product"])["results"]
        assert len(rows) == 1
        assert rows[0]["source"]["sku"] == "VAZA-42"
        assert rows[0]["url"] == url
        assert rows[0]["metadata"] == {"merchantSku": "VAZA-42", "kaspiSku": "958630914",
                                         "brand": "Марка", "status": "Активен", "stock": 7,
                                         "price": 12500, "has_image": True}
    assert len(search("a", "VAZA-42", doc_types=["product"])["results"][0]["text"]) <= 400


def test_reviewed_cards_features_link_and_caps(monkeypatch):
    product = {"merchantSku": "BLUE-1", "name": "Каталожная ваза",
               "url": "https://kaspi.kz/shop/p/vaza-119036852/"}
    payload = {"sku": "BLUE-1", "category_name": "Вазы", "brand_name": "Мастерская",
               "features": {"color": "синий", "material": ["стекло", "металл"], "width": 12},
               "feature_meta": {"color": {"name": "Цвет"}, "material": {"name": "Материал"}},
               "description": "Украшение дома. " * 200}
    drafts = [{"id": "approved", "status": "approved", "raw_title": "Черновая ваза", "draft_payload": payload},
              {"id": "submitted", "status": "submitted", "raw_title": "Другая вещь",
               "draft_payload": {**payload, "sku": "LONE-2", "features": {"color": "зелёный"}}},
              {"id": "draft", "status": "draft", "raw_title": "Секретный черновик",
               "draft_payload": {**payload, "sku": "DRAFT-3", "features": {"color": "фиолетовый"}}},
              {"id": "review", "status": "needs_review", "raw_title": "Непроверенный",
               "draft_payload": {**payload, "sku": "REVIEW-4", "features": {"color": "оранжевый"}}}]
    sources(monkeypatch, {"a": [product]}, lambda store_id, status: [d for d in drafts if d["status"] == status])
    rows = search("a", "цвет синий", doc_types=["card"])["results"]
    assert rows[0]["source"]["sku"] == "BLUE-1"
    assert rows[0]["source"] == {"doc_id": "card:approved", "doc_type": "card", "sku": "BLUE-1"}
    assert "Цвет: синий" in rows[0]["text"]
    assert "Материал: стекло, металл" in rows[0]["text"]
    assert "width: 12" in rows[0]["text"]
    assert rows[0]["url"] == product["url"]
    assert rows[0]["metadata"]["category"] == "Вазы"
    assert len(rows[0]["text"]) <= 700
    assert not search("a", "фиолетовый", doc_types=["card"])["results"]
    assert not search("a", "оранжевый", doc_types=["card"])["results"]
    lone = search("a", "зелёный", doc_types=["card"])["results"]
    assert len(lone) == 1 and lone[0]["source"]["sku"] == "LONE-2"
    assert "url" not in lone[0]
    documents = build_documents([product], [], [], [], drafts)
    assert all(len(doc.text) <= (1500 if doc.doc_type == "card" else 800) for doc in documents)
    assert [doc.doc_type for doc in documents].count("card") == 2


def test_real_draft_loader_is_store_scoped(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'drafts.db').as_posix()}")
    DraftProductDB.__table__.create(engine)
    session_factory = sessionmaker(engine)
    monkeypatch.setattr(validator, "_session", session_factory)
    with session_factory() as session:
        for store_id, color in (("a", "синий"), ("b", "красный")):
            session.add(DraftProductDB(store_id=store_id, photo_path="unused", raw_title="Ваза",
                status="approved", draft_payload=json.dumps({"sku": f"{store_id}-1",
                    "features": {"color": color}, "feature_meta": {"color": {"name": "Цвет"}}},
                    ensure_ascii=False)))
        session.commit()
    clear_cache()

    async def catalog(store_id, only_active):
        return []

    monkeypatch.setattr(builtin_tools, "_annotated_catalog", catalog)
    monkeypatch.setattr(state_helpers, "_load_sales_daily", lambda store_id, days: [])
    monkeypatch.setattr(state_helpers, "_load_price_war_snapshot", lambda store_id: [])
    monkeypatch.setattr(state_helpers, "_load_dumper_logs", lambda store_id, limit: [])
    try:
        assert [r["source"]["sku"] for r in search("a", "синий", doc_types=["card"])["results"]] == ["a-1"]
        assert search("b", "синий", doc_types=["card"])["results"] == []
        assert [r["source"]["sku"] for r in search("b", "красный", doc_types=["card"])["results"]] == ["b-1"]
    finally:
        engine.dispose()


def test_draft_source_failure_keeps_product_search(monkeypatch):
    def fail(store_id, status):
        raise RuntimeError("draft loader unavailable")

    sources(monkeypatch, {"a": [{"merchantSku": "SAFE-1", "name": "Надёжная ваза"}]}, fail)
    assert [r["source"]["sku"] for r in search("a", "SAFE-1")["results"]] == ["SAFE-1"]


def test_card_access_and_response_limits(monkeypatch):
    long_url = "https://kaspi.kz/shop/p/" + "a" * 900
    product = {"merchantSku": "LONG-1", "name": "Ваза", "url": long_url,
               "brand": "Б" * 300}
    draft = {"id": "long", "status": "approved", "raw_title": "Ваза",
             "draft_payload": {"sku": "LONG-1", "features": {"color": "синий"},
                               "feature_meta": {"color": {"name": "Цвет"}}}}
    sources(monkeypatch, {"a": [product]}, lambda store_id, status: [draft] if status == "approved" else [])
    response = search("a", "синий", doc_types=["card"])["results"][0]
    assert len(response["text"]) <= 700
    assert len(response["url"]) == 500
    blocked = asyncio.run(tools.execute("search_store_knowledge",
        {"query": "синий", "doc_types": ["card"]},
        ToolContext(store_id="a", sections=frozenset({"assistant", "overview"}))))["result"]
    assert blocked == {"results": [], "note": "Нет доступа к запрошенным источникам"}
