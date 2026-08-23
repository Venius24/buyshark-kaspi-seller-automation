"""
Тесты инварианта полноты снимка каталога.

Каждый тест здесь описывает способ уронить каталог владельца. Проверяется одно
и то же свойство: сборщик ОТКАЗЫВАЕТСЯ, а не отдаёт частичный результат —
частичный прайс-лист снимает с продажи всё, чего в нём нет (HANDOFF.md §3.67).
"""
import pytest

from backend.dumper import feed_snapshot
from backend.dumper.feed_snapshot import (
    CatalogSnapshot,
    FeedSnapshotRefused,
    build_catalog_snapshot,
)
from backend.kaspi_wrapper import AssortmentTruncated


def _offer(sku="D844-0005", price=590, stock=64.0, available=True, **extra):
    base = {
        "merchantSku": sku,
        "sku": "147776238",
        "name": f"Товар {sku}",
        "brand": "barkhan",
        "price_num": price,
        "stock": stock,
        "available": available,
        "status": "Активен" if available else "Архив",
    }
    base.update(extra)
    return base


def _catalog(n=100, **over):
    return [_offer(sku=f"SKU-{i:04d}", **over) for i in range(n)]


@pytest.fixture
def stub_kaspi(monkeypatch):
    """Подменяет выкачку каталога и merchant_id; отдаёт настраиваемый контейнер."""

    state = {"products": _catalog(), "raises": None}

    def fake_sync(token, store_id=None, strict=False):
        if state["raises"] is not None:
            raise state["raises"]
        return list(state["products"])

    monkeypatch.setattr(feed_snapshot.KaspiAPI, "sync_merchant_assortment", staticmethod(fake_sync))
    monkeypatch.setattr(feed_snapshot.KaspiAPI, "get_merchant_id", staticmethod(lambda token: "20186986"))
    return state


def test_happy_path(stub_kaspi):
    snap = build_catalog_snapshot("tok", "store-1", baseline_count=100)
    assert isinstance(snap, CatalogSnapshot)
    assert snap.size == 100
    assert snap.merchant_id == "20186986"


def test_refuses_when_pagination_truncated(stub_kaspi):
    """Обрыв выкачки — главный сценарий аварии: каталог целый, а список нет."""
    stub_kaspi["raises"] = AssortmentTruncated("HTTP 502 на странице 3", 240)
    with pytest.raises(FeedSnapshotRefused) as e:
        build_catalog_snapshot("tok", "store-1", baseline_count=1119)
    assert "оборвалась" in str(e.value)
    assert "240" in str(e.value)


def test_refuses_on_unreadable_price(stub_kaspi):
    products = _catalog(50)
    products[7]["price_num"] = 0
    stub_kaspi["products"] = products
    with pytest.raises(FeedSnapshotRefused) as e:
        build_catalog_snapshot("tok", "store-1", baseline_count=50)
    assert "без цены: 1" in str(e.value)


def test_refuses_on_missing_merchant_sku(stub_kaspi):
    products = _catalog(50)
    del products[3]["merchantSku"]
    stub_kaspi["products"] = products
    with pytest.raises(FeedSnapshotRefused) as e:
        build_catalog_snapshot("tok", "store-1", baseline_count=50)
    assert "без merchantSku: 1" in str(e.value)


def test_refuses_on_negative_stock(stub_kaspi):
    products = _catalog(50)
    products[11]["stock"] = -3
    stub_kaspi["products"] = products
    with pytest.raises(FeedSnapshotRefused) as e:
        build_catalog_snapshot("tok", "store-1", baseline_count=50)
    assert "без остатка: 1" in str(e.value)


def test_refuses_on_duplicate_sku(stub_kaspi):
    products = _catalog(50)
    products[10]["merchantSku"] = products[9]["merchantSku"]
    stub_kaspi["products"] = products
    with pytest.raises(FeedSnapshotRefused) as e:
        build_catalog_snapshot("tok", "store-1", baseline_count=50)
    assert "дубли" in str(e.value)


def test_refuses_on_sharp_shrink(stub_kaspi):
    """Выкачка «успешна», но каталог похудел вдесятеро — это решает человек."""
    stub_kaspi["products"] = _catalog(100)
    with pytest.raises(FeedSnapshotRefused) as e:
        build_catalog_snapshot("tok", "store-1", baseline_count=1119)
    assert "резко меньше" in str(e.value)


def test_tolerates_shrink_within_threshold(stub_kaspi):
    stub_kaspi["products"] = _catalog(95)
    snap = build_catalog_snapshot("tok", "store-1", baseline_count=100)
    assert snap.size == 95


def test_growth_is_allowed_but_noted(stub_kaspi):
    stub_kaspi["products"] = _catalog(140)
    snap = build_catalog_snapshot("tok", "store-1", baseline_count=100)
    assert snap.size == 140
    assert any("вырос" in n for n in snap.notes)


def test_refuses_almost_empty_catalog(stub_kaspi):
    stub_kaspi["products"] = _catalog(2)
    with pytest.raises(FeedSnapshotRefused) as e:
        build_catalog_snapshot("tok", "store-1", baseline_count=None)
    assert "подозрительно мало" in str(e.value)


def test_archived_offers_are_excluded_not_counted_as_loss(stub_kaspi):
    """Снятые карточки в файл не идут, но и усадкой не считаются."""
    products = _catalog(100) + [_offer(sku=f"OLD-{i}", available=False) for i in range(400)]
    stub_kaspi["products"] = products
    snap = build_catalog_snapshot("tok", "store-1", baseline_count=100)
    assert snap.size == 100
    assert snap.archived_count == 400


def test_baseline_taken_from_cache_when_not_given(stub_kaspi, monkeypatch):
    monkeypatch.setattr(
        "backend.state_helpers._load_products",
        lambda store_id: _catalog(1119),
    )
    stub_kaspi["products"] = _catalog(100)
    with pytest.raises(FeedSnapshotRefused) as e:
        build_catalog_snapshot("tok", "store-1")
    assert "резко меньше" in str(e.value)


def test_missing_cache_is_noted_not_fatal(stub_kaspi, monkeypatch):
    monkeypatch.setattr("backend.state_helpers._load_products", lambda store_id: [])
    snap = build_catalog_snapshot("tok", "store-1")
    assert snap.size == 100
    assert any("не с чем сравнить" in n for n in snap.notes)


def test_snapshot_feeds_the_serializer(stub_kaspi):
    """Снимок и сериализатор должны стыковаться без переходников."""
    from backend.dumper.xml_feed import generate_xml_feed

    snap = build_catalog_snapshot("tok", "store-1", baseline_count=100)
    xml = generate_xml_feed(snap.merchant_id, snap.offers).decode("utf-8")
    assert xml.count("<offer sku=") == 100
    assert "<merchantid>20186986</merchantid>" in xml
