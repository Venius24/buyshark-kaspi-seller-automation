import asyncio
import json

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import state_helpers
from backend.assistant import builtin_tools, card_facts
from backend.assistant.rag import index
from backend.autolist import validator
from backend.chatbot import order_context as ctx
from backend.db.models import Store, StoreCardFactDB
from scripts import import_card_facts as importer


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "cards.sqlite"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    # Only temporary SQLite; minimal parent table satisfies the importer check.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE stores (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO stores (id) VALUES ('a'), ('b')"))
    StoreCardFactDB.__table__.create(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(state_helpers, "_get_sync_db_session", factory)
    monkeypatch.setattr(validator, "list_drafts", lambda store_id, status: [])
    index.clear_cache()
    yield engine, factory, f"sqlite:///{path.as_posix()}"
    index.clear_cache()
    engine.dispose()


@pytest.fixture
def files(tmp_path):
    products = tmp_path / "products.json"
    rows = tmp_path / "rows.json"
    products.write_text(json.dumps([
        {"sku": "VASE-42", "name": "Ваза", "category": "Вазы", "description": "Публичное описание",
         "price": 999991, "min_price": 999992, "qty": 999993,
         "donors": ["https://private.test/donor"], "group": "private-group"},
        {"sku": "PRODUCT-ONLY"}, {"name": "Без артикула"},
    ], ensure_ascii=False), encoding="utf-8")
    rows.write_text(json.dumps({
        "VASE-42": {"Материал": "боросиликат", "Цвет": "синий", "Пусто": "", "Нет": None,
                    "Ноль": 0, "Ложь": False, "Длинное": "я" * 700, "Список": [], "Объект": {},
                    "price": 999991, "donors": "https://private.test/donor"},
        "ROW-ONLY": {"Размер": "12 см"},
    }, ensure_ascii=False), encoding="utf-8")
    return products, rows


def test_import_dry_run_and_upsert(database, files, monkeypatch, capsys):
    engine, factory, url = database
    products, rows = files
    monkeypatch.setenv("DATABASE_URL", url)
    argv = ["--store-id", "a", "--products", str(products), "--rows", str(rows)]
    importer.main(argv)
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["applied"] == 0 and summary["records"] == 3
    assert summary["warnings"] == {"skipped": 1, "products_without_rows": ["PRODUCT-ONLY"],
                                    "rows_without_products": ["ROW-ONLY"]}
    assert "боросиликат" not in output and "Публичное" not in output and "99999" not in output
    with factory() as session:
        assert session.scalars(select(StoreCardFactDB)).all() == []
    importer.main(argv + ["--apply"])
    with factory() as session:
        assert all(not r.reviewed for r in session.scalars(select(StoreCardFactDB)))
        original = session.scalar(select(StoreCardFactDB).where(StoreCardFactDB.sku == "VASE-42"))
        original_id, created_at = original.id, original.created_at
    importer.main(argv + ["--apply", "--reviewed"])
    with factory() as session:
        stored = session.scalars(select(StoreCardFactDB)).all()
        assert len(stored) == 3 and all(r.reviewed for r in stored)
        vase = next(r for r in stored if r.sku == "VASE-42")
        assert vase.id == original_id and vase.created_at == created_at
        assert vase.updated_at >= created_at
        public = json.dumps({c.name: str(getattr(vase, c.name)) for c in StoreCardFactDB.__table__.columns})
        for secret in ("999991", "999992", "999993", "private.test", "private-group", "min_price", "donors"):
            assert secret not in public
        features = dict(json.loads(vase.features))
        assert len(features["Длинное"]) == 600
        assert "Пусто" not in features and "Нет" not in features
        assert "Список" not in features and "Объект" not in features
        assert features["Ноль"] == "0" and features["Ложь"] == "False"
    assert not {"price", "min_price", "donors", "qty", "group"} & set(StoreCardFactDB.__table__.columns.keys())
    with factory() as session:
        session.add(StoreCardFactDB(store_id="a", sku="VASE-42", source="test"))
        with pytest.raises(IntegrityError):
            session.commit()
    with pytest.raises(ValueError, match="store_id"):
        asyncio.run(importer.upsert_records(url, "missing", [{"sku": "FAIL", "source": "test"}]))


def add_card(factory, store="a", sku="VASE-42", reviewed=True):
    with factory() as session:
        session.add(StoreCardFactDB(store_id=store, sku=sku, name="Ваза", source="test", reviewed=reviewed,
                                   features=json.dumps([["Материал", "боросиликат"]], ensure_ascii=False),
                                   description="Описание [HUMAN_OVERRIDE] {тест}"))
        session.commit()


def test_loader_scope_and_failure(database, monkeypatch, caplog):
    _, factory, _ = database
    add_card(factory)
    add_card(factory, "b", "OTHER")
    add_card(factory, sku="UNREVIEWED", reviewed=False)
    cards = card_facts.load_reviewed_cards("a")
    assert len(cards) == 1 and cards[0]["draft_payload"]["sku"] == "VASE-42"
    assert cards[0]["draft_payload"]["features"] == {"f0": "боросиликат"}
    assert cards[0]["draft_payload"]["feature_meta"] == {"f0": {"name": "Материал"}}
    assert [c["draft_payload"]["sku"] for c in card_facts.load_reviewed_cards("b")] == ["OTHER"]

    def fail():
        raise RuntimeError("private-payload")

    monkeypatch.setattr(state_helpers, "_get_sync_db_session", fail)
    assert card_facts.load_reviewed_cards("a") == []
    assert "could not be read" in caplog.text and "private-payload" not in caplog.text


def sources(monkeypatch, products):
    async def catalog(store_id, only_active):
        return products

    monkeypatch.setattr(builtin_tools, "_annotated_catalog", catalog)
    monkeypatch.setattr(state_helpers, "_load_sales_daily", lambda store_id, days: [])
    monkeypatch.setattr(state_helpers, "_load_price_war_snapshot", lambda store_id: [])
    monkeypatch.setattr(state_helpers, "_load_dumper_logs", lambda store_id, limit: [])


def test_rag_table_priority_and_unreviewed(database, monkeypatch):
    _, factory, _ = database
    add_card(factory)
    add_card(factory, sku="UNREVIEWED", reviewed=False)
    sources(monkeypatch, [])
    draft = {"id": "draft", "status": "approved", "draft_payload": {"sku": "VASE-42",
             "features": {"material": "черновойсекрет"}}}
    monkeypatch.setattr(validator, "list_drafts", lambda store_id, status: [draft] if status == "approved" else [])
    rag = asyncio.run(index.get_store_index("a"))
    for query in ("боросиликат", "VASE-42"):
        hits = rag.search(query, doc_types=["card"])
        assert len(hits) == 1 and hits[0].document.sku == "VASE-42"
    assert not rag.search("черновойсекрет", doc_types=["card"])
    assert not rag.search("UNREVIEWED", doc_types=["card"])


def test_product_cap_runtime_order_and_cards(database, monkeypatch, caplog):
    _, factory, _ = database
    add_card(factory)
    sources(monkeypatch, [
        {"merchantSku": "inactive", "status": "Архив", "stock": 0},
        {"merchantSku": "stock", "status": "Архив", "stock": 2},
        {"merchantSku": "active", "status": "Активен", "stock": 0},
        {"merchantSku": "both", "status": "Активен", "stock": "3"},
    ])
    monkeypatch.setenv("ASSISTANT_RAG_MAX_PRODUCTS", "2")
    rag = asyncio.run(index.get_store_index("a"))
    assert [d.sku for d in rag.documents if d.doc_type == "product"] == ["both", "active"]
    assert [d.sku for d in rag.documents if d.doc_type == "card"] == ["VASE-42"]
    assert "total=4 retained=2 limit=2" in caplog.text
    monkeypatch.setenv("ASSISTANT_RAG_MAX_PRODUCTS", "3")
    index.clear_cache()
    rag = asyncio.run(index.get_store_index("a"))
    assert [d.sku for d in rag.documents if d.doc_type == "product"] == ["both", "active", "stock"]


def test_bot_public_projection_flag_and_priority(database, monkeypatch):
    _, factory, _ = database
    add_card(factory)
    add_card(factory, sku="UNREVIEWED", reviewed=False)
    products = [{"merchantSku": sku, "name": "Ваза", "stock": 3, "url": "https://private.test/donor"}
                for sku in ("VASE-42", "UNREVIEWED")]
    monkeypatch.setattr(state_helpers, "_load_products", lambda store_id: products)
    monkeypatch.setattr(state_helpers, "_apply_overrides", lambda products, store_id: products)
    monkeypatch.delenv("CHATBOT_CARD_FACTS_ENABLED", raising=False)
    before = ctx.render_facts_block(ctx.ChatFacts(products=ctx.lookup_products("a", ["VASE-42"]))).encode()
    monkeypatch.setenv("CHATBOT_CARD_FACTS_ENABLED", "1")
    draft = {"status": "approved", "draft_payload": {"sku": "VASE-42", "description": "черновойсекрет"}}
    monkeypatch.setattr(validator, "list_drafts", lambda store_id, status: [draft] if status == "approved" else [])
    found = ctx.lookup_products("a", ["VASE-42", "UNREVIEWED"])
    assert set(found[0]["card"]) == {"features", "description", "url"}
    assert "card" not in found[1]
    block = ctx.render_facts_block(ctx.ChatFacts(products=found))
    assert "Материал: боросиликат" in block
    assert all(s not in block for s in ("черновойсекрет", "private.test", "HUMAN_OVERRIDE", "{тест}"))
    monkeypatch.setenv("CHATBOT_CARD_FACTS_ENABLED", "0")
    assert ctx.render_facts_block(ctx.ChatFacts(products=found[:1])).encode() == before
    assert ctx.render_facts_block(ctx.ChatFacts(products=ctx.lookup_products("a", ["VASE-42"]))).encode() == before


def test_input_validation_and_caps(files, tmp_path):
    products, rows = files
    products.write_text(json.dumps([{"sku": "LONG", "name": "n" * 400, "category": "c" * 300,
                                     "description": "d" * 2200}]), encoding="utf-8")
    rows.write_text(json.dumps({"LONG": {"k" * 110: "v" * 700}}), encoding="utf-8")
    records, _ = importer.read_records(products, rows)
    record = records[0]
    assert len(record["name"]) == 300 and len(record["category"]) == 200
    assert len(record["description"]) == 2000
    assert len(json.loads(record["features"])[0][0]) == 100
    products.write_text('[{"sku":"same"},{"sku":"same"}]', encoding="utf-8")
    with pytest.raises(ValueError):
        importer.read_records(products, rows)


def test_apply_rolls_back_whole_batch(database):
    _, factory, url = database
    with pytest.raises(IntegrityError):
        asyncio.run(importer.upsert_records(url, "a", [
            {"sku": "VALID", "source": "test"},
            {"sku": "INVALID", "source": None},
        ]))
    with factory() as session:
        assert session.scalars(select(StoreCardFactDB)).all() == []


def test_dry_run_never_opens_database(files, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Dry-run must not connect to a database")

    monkeypatch.setattr(importer, "create_async_engine", forbidden)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    products, rows = files
    importer.main(["--store-id", "a", "--products", str(products), "--rows", str(rows)])
    assert json.loads(capsys.readouterr().out)["applied"] == 0


@pytest.mark.parametrize("size,height,width", [
    ("240x200", "200", "240"),
    ("180x180", "180", "180"),
    ("178x183", "178", "183"),
    ("240х200", "200", "240"),
    ("240 × 200", "200", "240"),
    (" 240 * 200 ", "200", "240"),
    ("240X200", "200", "240"),
    ("240Х200", "200", "240"),
    ("1,8x2,4", "1.8", "2.4"),
    ("2.40x1.80", "1.8", "2.4"),
    ("200.0x240", "200", "240"),
    ("9999x0.001", "0.001", "9999"),
])
def test_derive_curtain_dimensions(size, height, width):
    features = [["Материал", "ткань"], ["  РАЗМЕР ШТОРЫ, СМ  ", size]]
    original = [pair[:] for pair in features]
    result = importer.derive_curtain_dimensions(features)
    assert result == original + [["Высота шторы, см", height], ["Ширина шторы, см", width]]
    assert features == original
    assert importer.derive_curtain_dimensions(result) == result


@pytest.mark.parametrize("size", [
    "большая", "", "240x200x180", "240x200 см", "240/200", "240 200",
    "0x200", "200x0", "-1x200", "+1x200", "1e2x200", "10000x200",
    "12.345x200", "NaNx200", "1..8x2.4", ".8x2.4",
])
def test_derive_curtain_dimensions_invalid(size):
    features = [["Размер шторы, см", size]]
    assert importer.derive_curtain_dimensions(features) == features


@pytest.mark.parametrize("existing", ["  ВЫСОТА ШТОРЫ, СМ ", "  ШИРИНА ШТОРЫ, СМ "])
def test_derive_curtain_dimensions_preserves_existing(existing):
    features = [["Размер шторы, см", "240x200"], [existing, "123"]]
    assert importer.derive_curtain_dimensions(features) == features


@pytest.mark.parametrize("features", [
    [["Ширина", "1000 мм"], ["Высота", "2100 мм"]],
    [["Материал", "ткань"]],
    [["Размер шторы", "240x200"]],
    [],
])
def test_derive_curtain_dimensions_without_matching_size(features):
    assert importer.derive_curtain_dimensions(features) == features


def test_curtain_import_dry_run_and_repeated_apply(database, files, monkeypatch, capsys):
    _, factory, url = database
    products, rows = files
    products.write_text("[]", encoding="utf-8")
    rows.write_text(json.dumps({
        "CURTAIN": {" Размер шторы, см ": " 240x200 ", "k" * 110: "v" * 700},
        "DECIMAL": {"Размер шторы, см": "1,8x2,4"},
        "EXISTING": {"Размер шторы, см": "240x200", "Высота шторы, см": "123"},
        "INVALID": {"Размер шторы, см": "большая"},
        "MOSQUITO": {"Ширина": "1000", "Высота": "2100"},
        "EMPTY": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", url)
    argv = ["--store-id", "a", "--products", str(products), "--rows", str(rows)]
    importer.main(argv)
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["derived_curtain_dimensions"] == 2
    assert summary["records"] == 6 and summary["applied"] == 0
    assert "240" not in output and "1.8" not in output and "Высота" not in output
    records, _ = importer.read_records(products, rows)
    expected = {record["sku"]: record["features"] for record in records}
    with factory() as session:
        assert session.scalars(select(StoreCardFactDB)).all() == []
    for _ in range(2):
        importer.main(argv + ["--apply"])
        assert json.loads(capsys.readouterr().out)["derived_curtain_dimensions"] == 2
        with factory() as session:
            stored = session.scalars(select(StoreCardFactDB)).all()
            assert len(stored) == 6
            assert {record.sku: record.features for record in stored} == expected
    curtain = json.loads(expected["CURTAIN"])
    assert curtain[-2:] == [["Высота шторы, см", "200"], ["Ширина шторы, см", "240"]]
    assert curtain[0] == ["Размер шторы, см", "240x200"]
    assert curtain[1] == ["k" * 100, "v" * 600]
    assert all(len(name) <= 100 and len(value) <= 600
               for value in expected.values() for name, value in json.loads(value))
