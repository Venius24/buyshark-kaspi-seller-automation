"""
Регрессия на баг «вкладка "Сняты" всегда пустая» (2026-08-15).

sync_merchant_assortment ходила в bff/offer-view/list только с `a=true`. У этого
параметра не семантика «включить фильтр», а ВЫБОР одной из двух непересекающихся
выборок: a=true — карточки в продаже, a=false — снятые (проверено вживую на
Бархане: 1150 + 423 = 1573 = ровно столько же, сколько отдаёт запрос без `a`).
Поэтому в кэше товаров не существовало ни одной записи со status="Архив", и
GET /api/products?status=archive гарантированно отдавал пустой список.
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.append(os.getcwd())

from backend.kaspi_wrapper import KaspiAPI
from backend.dumper.radar import RadarWorker


def _resp(offers):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"data": offers, "total": len(offers)}
    return r


ACTIVE = [{
    "masterSku": "111", "sku": "A-1", "price": 500, "title": "В продаже",
    "availabilities": [{"stockCount": 7, "storeId": "20186986_001"}],
    "available": True,
}]
ARCHIVED = [{
    "masterSku": "222", "sku": "B-1", "price": 0, "title": None,
    "availabilities": [], "available": False,
}]


def _run_sync():
    calls = []

    def fake_request(method, url, cookies, store_id=None, **kwargs):
        calls.append(kwargs.get("params", {}))
        active = kwargs.get("params", {}).get("a") == "true"
        return _resp(ACTIVE if active else ARCHIVED)

    with patch.object(KaspiAPI, "get_merchant_id", return_value="20186986"), \
         patch.object(KaspiAPI, "_load_mc_cookies", return_value={"mc-session": "x", "mc-sid": "y"}), \
         patch.object(KaspiAPI, "_request_with_mc_refresh", side_effect=fake_request):
        return KaspiAPI.sync_merchant_assortment(token="tok"), calls


def test_sync_walks_both_active_and_archived_selections():
    products, calls = _run_sync()
    assert [c["a"] for c in calls] == ["true", "false"]
    assert {p["merchantSku"] for p in products} == {"A-1", "B-1"}


def test_archived_offer_gets_archive_status_and_survives_missing_fields():
    products, _ = _run_sync()
    archived = next(p for p in products if p["merchantSku"] == "B-1")
    assert archived["status"] == "Архив"
    # У снятых карточек Kaspi нередко пустой availabilities и title=None —
    # это не должно ронять пагинацию (вживую такие в архиве Бархана есть).
    assert archived["stock"] == 0
    assert archived["name"] == "Товар B-1"
    assert next(p for p in products if p["merchantSku"] == "A-1")["status"] == "Активен"


def test_duplicate_offer_across_passes_is_not_doubled():
    def fake_request(method, url, cookies, store_id=None, **kwargs):
        return _resp(ACTIVE)  # один и тот же оффер в обеих выборках

    with patch.object(KaspiAPI, "get_merchant_id", return_value="20186986"), \
         patch.object(KaspiAPI, "_load_mc_cookies", return_value={"mc-session": "x", "mc-sid": "y"}), \
         patch.object(KaspiAPI, "_request_with_mc_refresh", side_effect=fake_request):
        products = KaspiAPI.sync_merchant_assortment(token="tok")
    assert len(products) == 1


def test_radar_skips_archived_products():
    """
    Отправка цены у исполнителя — это ON_SALE__PRICE_SAVE с available:"yes",
    то есть демпинг снятой карточки вернул бы её в продажу вопреки владельцу.
    """
    products = [
        {"merchantSku": "A-1", "sku": "111", "kaspiSku": "111", "status": "Активен"},
        {"merchantSku": "B-1", "sku": "222", "kaspiSku": "222", "status": "Архив"},
    ]
    configs = {
        "A-1": {"enabled": True, "cityCode": "750000000"},
        "B-1": {"enabled": True, "cityCode": "750000000"},
    }

    import asyncio

    async def _no_offers(*_a, **_kw):
        return []

    async def _no_health(*_a, **_kw):
        return None

    with patch("backend.dumper.radar.fetch_competitor_offers", side_effect=_no_offers), \
         patch("backend.dumper.radar._notify_radar_health", side_effect=_no_health):
        snapshot = asyncio.run(RadarWorker.scan_market(products, configs))

    assert set(snapshot.keys()) == {"A-1"}
