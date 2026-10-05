from fastapi.testclient import TestClient

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
