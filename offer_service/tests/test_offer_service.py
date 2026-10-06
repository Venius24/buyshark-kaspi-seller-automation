import re
from pathlib import Path
from urllib.parse import unquote

from fastapi.testclient import TestClient
import pytest

from backend.billing.catalog import PLANS
from offer_service import db, tariffs
from offer_service.locale import KZ
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


def test_kazakh_offer_uses_same_data_and_shareable_language_url(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "offers.db")
    db.init_db()
    db.upsert_offer({
        "slug": "bilingual", "client_name": "Тест дүкені", "product_count": 31,
        "tier_override": "hunter", "first_month_price": 3000, "monthly_price": 5000,
        "orders_label": "120+", "years_label": "5 лет",
        "categories": [{"name": "Электроника", "name_kz": "Электроника", "count": 31}],
        "sample_product": {
            "sku": "12345", "title": "Планшет", "title_kz": "Планшет",
            "url": "https://kaspi.kz/shop/p/tablet-12345/",
            "price": 38804, "competitors": [{"price": 40000}],
            "observed_at": "2026-10-06T13:00:00+00:00",
        },
    })
    client = TestClient(app)
    ru = client.get("/bilingual?lang=ru")
    kz = client.get("/bilingual?lang=kz")
    assert ru.status_code == kz.status_code == 200
    assert '<html lang="ru">' in ru.text
    assert '<html lang="kk">' in kz.text
    assert 'href="/bilingual?lang=kz"' in kz.text
    assert 'href="/bilingual?lang=ru"' in kz.text
    assert "Күнделікті жұмыс азырақ." in kz.text
    assert "5 жыл" in kz.text
    assert "3 000 ₸" in ru.text and "3 000 ₸" in kz.text
    assert "5 000 ₸" in ru.text and "5 000 ₸" in kz.text
    assert "38 804 ₸" in ru.text and "38 804 ₸" in kz.text
    assert "Первый оплаченный месяц" not in kz.text
    assert "коммерциялық ұсынысты алдым" in unquote(kz.text)
    assert '<html lang="ru">' in client.get("/bilingual?lang=unknown").text


def test_all_offer_copy_and_tariff_features_have_kazakh_translations():
    template = (Path(__file__).parents[1] / "templates" / "offer.html").read_text(encoding="utf-8")
    used_keys = set(re.findall(r"\btr\('([^']+)'", template))
    feature_keys = {
        copy for tier in tariffs.TIERS for feature in tier.features for copy in feature
    }
    assert (used_keys | feature_keys) - KZ.keys() == set()


def test_kazakh_text_for_unlimited_and_unknown_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "offers.db")
    db.init_db()
    db.upsert_offer({"slug": "giga-kz", "client_name": "Үлкен дүкен", "product_count": 2500})
    db.upsert_offer({"slug": "unknown-kz", "client_name": "Жаңа дүкен"})
    client = TestClient(app)

    giga = client.get("/giga-kz?lang=kz")
    unknown = client.get("/unknown-kz?lang=kz")
    assert giga.status_code == unknown.status_code == 200
    assert "келісім бойынша" in giga.text
    assert "2 000 тауарлық шегінен асады" in giga.text
    assert "Каталогтің нақты көлемін қосылу кезінде анықтаймыз" in unknown.text
