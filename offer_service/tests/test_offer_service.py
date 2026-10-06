from fastapi.testclient import TestClient
import pytest

from backend.billing.catalog import PLANS
from offer_service import db, tariffs
from offer_service.main import app


def test_offer_tariffs_match_billing_catalog():
    plan_codes = ("START", "HUNTER", "PREDATOR", "PREDATOR_PLUS", "GIGA")
    assert len(tariffs.TIERS) == len(plan_codes)
    for tier, code in zip(tariffs.TIERS, plan_codes):
        plan = PLANS[code]
        assert tier.name == plan.name
        assert tier.limit == plan.max_products
        expected_price = None if plan.monthly_price_minor is None else plan.monthly_price_minor // 100
        assert tier.price == expected_price


def test_offer_page_renders_from_persistent_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "offers.db")
    db.init_db()
    db.upsert_offer({"slug": "pipeline-smoke", "client_name": "Тестовый магазин", "product_count": 674})

    client = TestClient(app)
    assert client.get("/healthz").text == "ok"
    response = client.get("/pipeline-smoke")
    assert response.status_code == 200
    assert "Тестовый магазин" in response.text
    assert "Хищник" in response.text
    assert client.get("/missing-offer").status_code == 404


@pytest.mark.parametrize(
    ("count", "tier", "first", "monthly"),
    [
        (1, "start", 1000, 3000),
        (30, "start", 1000, 3000),
        (31, "hunter", 3000, 5000),
        (100, "hunter", 3000, 5000),
        (101, "hunter", 5000, 8000),
        (200, "hunter", 5000, 8000),
    ],
)
def test_newcomer_price_boundaries(count, tier, first, monthly):
    assert tariffs.newcomer_terms(count) == (tier, first, monthly)


def test_fixed_price_offer_migrates_and_renders_without_percentage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "offers.db")
    db.init_db()
    db.upsert_offer({"slug": "newcomer", "client_name": "Новый магазин", "product_count": 31,
                     "tier_override": "hunter", "first_month_price": 3000,
                     "monthly_price": 5000})

    client = TestClient(app)
    response = client.get("/newcomer")
    assert response.status_code == 200
    assert "3 000 ₸" in response.text
    assert "затем 5 000 ₸/мес" in response.text
    assert "со скидкой 30%" not in response.text
    assert "больше, чем помещается в тариф «Бодрый старт»" not in response.text

    # Existing offers databases are migrated in place without losing their rows.
    with db.get_conn() as conn:
        conn.execute("ALTER TABLE offers RENAME TO old_offers")
        conn.execute("CREATE TABLE offers AS SELECT slug, client_name, shop_url, product_count, "
                     "orders_label, years_label, categories, discount_pct, tier_override, "
                     "created_at, updated_at FROM old_offers")
        conn.execute("DROP TABLE old_offers")
    db.init_db()
    assert db.get_offer("newcomer")["first_month_price"] is None


def test_offer_uses_verified_seller_price_in_product_section(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "offers.db")
    db.init_db()
    sample = {
        "sku": "197490648", "title": "Планшет для проверки",
        "url": "https://kaspi.kz/shop/p/tablet-197490648/?c=750000000",
        "image_url": "https://resources.cdn-kaspi.kz/img/m/example.jpeg",
        "price": 38804, "competitors": [{"name": "Другой магазин", "price": 40000}],
        "observed_at": "2026-10-06T13:00:00+00:00", "city": "Алматы",
    }
    db.upsert_offer({"slug": "actual-price", "client_name": "Магазин", "product_count": 31,
                     "sample_product": sample})
    assert db.get_offer("actual-price")["sample_product"] == sample

    response = TestClient(app).get("/actual-price")
    assert response.status_code == 200
    assert "Планшет для проверки" in response.text
    assert "38 804 ₸" in response.text
    assert "40 000 ₸" in response.text
    assert "12 490 ₸" not in response.text
    assert "Цена вашего магазина на момент проверки" in response.text
