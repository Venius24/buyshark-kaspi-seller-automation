import asyncio
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend import state_helpers
from backend.assistant import builtin_tools, card_facts, rag_tool, tools
from backend.assistant.rag.bm25 import BM25Retriever
from backend.assistant.rag.documents import build_documents
from backend.assistant.rag.index import clear_cache
from backend.assistant.tools import ToolContext
from backend.autolist import validator
from backend.db.models import MasterStockGroupDB


def group(group_id="g1", **overrides):
    return {"group_id": group_id, "group_name": "Лазурные вазы", "shared_stock": 12.5,
            "stop_qty": 3, "linked_skus": ["VASE-42"], "is_active": True, **overrides}


def product(sku="VASE-42", **overrides):
    return {"merchantSku": sku, "name": "Ваза стеклянная", **overrides}


def card(**overrides):
    return {"id": "c1", "status": "approved", "draft_payload": {
        "sku": "VASE-42", "features": {"material": "стекло"},
        "description": "Публичное описание", **overrides}}


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    clear_cache()

    async def catalog(store_id, only_active):
        return [product()]

    monkeypatch.setattr(builtin_tools, "_annotated_catalog", catalog)
    monkeypatch.setattr(state_helpers, "_load_sales_daily", lambda store_id, days: [])
    monkeypatch.setattr(state_helpers, "_load_price_war_snapshot", lambda store_id: [])
    monkeypatch.setattr(state_helpers, "_load_dumper_logs", lambda store_id, limit: [])
    monkeypatch.setattr(validator, "list_drafts", lambda store_id, status: [])
    monkeypatch.setattr(card_facts, "load_reviewed_cards", lambda store_id: [card()])
    # Any accidental DB access in these tests fails instead of using application DB.
    def forbidden_db():
        raise AssertionError("Unexpected application DB access")
    monkeypatch.setattr(state_helpers, "_get_sync_db_session", forbidden_db)
    yield
    clear_cache()


def search(store_id="a", query="Лазурные", **kwargs):
    return asyncio.run(tools.execute("search_store_knowledge", {"query": query, **kwargs},
                                    ToolContext(store_id=store_id)))["result"]


@pytest.mark.parametrize("encoded", [False, True])
def test_group_document_search_and_product_card_metadata(encoded):
    linked = json.dumps(["VASE-42"]) if encoded else ["VASE-42"]
    docs = build_documents([product()], [], [], [], [card()],
                           groups={"g1": group(linked_skus=linked)})
    index = BM25Retriever(docs)
    for query in ("Лазурные вазы", "VASE-42"):
        hits = index.search(query, doc_types=["master_group"])
        assert [hit.document.doc_id for hit in hits] == ["master_group:g1"]
    doc = next(doc for doc in docs if doc.doc_type == "master_group")
    assert doc.sku == "g1"
    assert doc.metadata == {"group_id": "g1", "group_name": "Лазурные вазы",
                            "shared_stock": 12.5, "stop_qty": 3, "linked_count": 1}
    for fragment in ("общий остаток 12.5", "стоп-остаток 3", "число артикулов 1", "Ваза стеклянная"):
        assert fragment in doc.text
    for doc in docs:
        if doc.doc_type in ("product", "card"):
            assert doc.metadata["master_group"] == "Лазурные вазы"
            assert doc.metadata["master_group_id"] == "g1"
            assert "master_group_count" not in doc.metadata
            assert "мастер-группа Лазурные вазы" in doc.text


def test_inactive_group_excluded():
    docs = build_documents([product()], [], [], [], [card()], groups={"g1": group(is_active=False)})
    assert {doc.doc_type for doc in docs} == {"product", "card"}
    assert all("master_group" not in doc.metadata for doc in docs)
    assert BM25Retriever(docs).search("Лазурные") == []


def test_multiple_groups_order_and_text_limits():
    docs = build_documents([product(name="Ваза" * 300)], [], [], [],
                           [card(description="Описание" * 400)], groups={
                               "z": group("z", group_name="Поздняя"),
                               "a": group("a", group_name="Ранняя" * 50)})
    for doc in docs:
        if doc.doc_type in ("product", "card"):
            assert doc.metadata["master_group_id"] == "a"
            assert doc.metadata["master_group_count"] == 2
            assert doc.text.startswith("мастер-группа " + ("Ранняя" * 50)[:60] + " ")
        assert len(doc.text) <= (800 if doc.doc_type == "product" else 1500)
    assert "стекло" in next(doc.text for doc in docs if doc.doc_type == "card")


def test_member_list_and_product_name_limits():
    skus = [f"MEMBER-{i:02}" for i in range(60)]
    docs = build_documents([product(sku, name=f"Имя_{i:02}") for i, sku in enumerate(skus)],
                           [], [], [], groups={"g1": group(linked_skus=skus)})
    doc = next(doc for doc in docs if doc.doc_type == "master_group")
    assert doc.metadata["linked_count"] == 60
    assert all(sku in doc.text for sku in skus[:40])
    assert all(sku not in doc.text for sku in skus[40:])
    assert "и ещё 20" in doc.text
    assert all(f"Имя_{i:02}" in doc.text for i in range(10))
    assert "Имя_10" not in doc.text
    assert len(doc.text) <= 1500


def test_real_group_loader_store_isolation(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'groups.sqlite').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE stores (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO stores (id) VALUES ('a'), ('b')"))
    MasterStockGroupDB.__table__.create(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(state_helpers, "_get_sync_db_session", factory)
    try:
        with factory() as session:
            session.add_all([
                MasterStockGroupDB(group_id="ga", store_id="a", group_name="Лазурные вазы",
                                   shared_stock=12.5, stop_qty=3, linked_skus='["VASE-42"]'),
                MasterStockGroupDB(group_id="gb", store_id="b", group_name="Алые лампы",
                                   shared_stock=20, stop_qty=4, linked_skus='["VASE-42"]')])
            session.commit()
        assert set(state_helpers._load_master_stock_groups("a")) == {"ga"}
        assert set(state_helpers._load_master_stock_groups("b")) == {"gb"}
        for store, own, foreign in (("a", "ga", "Алые"), ("b", "gb", "Лазурные")):
            rows = search(store, "VASE-42", doc_types=["master_group"])["results"]
            assert [row["metadata"]["group_id"] for row in rows] == [own]
            assert search(store, foreign, doc_types=["master_group"])["results"] == []
            rows = search(store, "VASE-42", doc_types=["product", "card"])["results"]
            assert len(rows) == 2
            assert all(row["metadata"]["master_group_id"] == own for row in rows)
    finally:
        engine.dispose()


def test_group_loader_failure_keeps_other_sources(monkeypatch, caplog):
    def fail(store_id):
        raise RuntimeError("groups unavailable")
    monkeypatch.setattr(state_helpers, "_load_master_stock_groups", fail)
    rows = search(query="VASE-42")["results"]
    assert {row["source"]["doc_type"] for row in rows} == {"product", "card"}
    assert all("master_group" not in row["metadata"] for row in rows)
    assert "groups unavailable" in caplog.text


def test_tool_group_filter_metadata_and_access(monkeypatch):
    monkeypatch.setattr(state_helpers, "_load_master_stock_groups",
                        lambda store_id: {"g1": group(linked_skus=["VASE-42"] + [f"SKU-{i}" for i in range(60)])})
    rows = search(query="VASE-42", doc_types=["master_group"])["results"]
    assert len(rows) == 1
    assert rows[0]["source"]["doc_type"] == "master_group"
    assert rows[0]["metadata"] == {"group_id": "g1", "group_name": "Лазурные вазы",
                                   "shared_stock": 12.5, "stop_qty": 3, "linked_count": 61}
    assert 400 < len(rows[0]["text"]) <= 700
    blocked = asyncio.run(rag_tool.search_store_knowledge(
        {"query": "Лазурные", "doc_types": ["master_group"]},
        ToolContext(store_id="a", sections=frozenset({"assistant", "overview"}))))
    assert blocked["results"] == []


def test_legacy_build_documents_calls():
    assert build_documents([], [], [], []) == []
    assert len(build_documents([product()], [], [], [])) == 1
    assert {doc.doc_type for doc in build_documents([product()], [], [], [], [card()])} == {"product", "card"}
