"""
Регрессия на баг "Цена не указана" (2026-07-29): плоское поле offer["price"] у Kaspi
BFF bff/offer-view/list — денормализованный кэш, который у части офферов отдаёт 0
даже когда цена реально назначена (подтверждено вживую на проде: 35/100 офферов
реального каталога buyshark). Реальная цена в этом случае лежит в cityPrices/
minPrice/rangePrice/allCityPrices — sync_merchant_assortment обязана их проверять,
иначе price_num=0 попадает в brain.py как my_current_price и ломает демпинг.
"""
from unittest.mock import patch, MagicMock

from backend.kaspi_wrapper import KaspiAPI


def _make_response(offers, total=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": offers, "total": total if total is not None else len(offers)}
    return resp


def _run_sync(offers):
    with patch("backend.state_helpers._load_session", return_value={"merchant_id": "20186986"}), \
         patch.object(KaspiAPI, "_load_mc_cookies", return_value={"mc-session": "x", "mc-sid": "y"}), \
         patch.object(KaspiAPI, "_request_with_mc_refresh", side_effect=[_make_response(offers), _make_response([])]):
        return KaspiAPI.sync_merchant_assortment(token="tok")


def test_flat_price_zero_recovered_from_city_prices():
    offers = [{
        "masterSku": "142591595",
        "sku": "S517-0053++",
        "price": 0,
        "oldprice": 0,
        "availabilities": [{"available": "yes", "storeId": "20186986_001", "stockCount": 5}],
        "cityPrices": [{"value": 457, "cityId": "750000000", "oldprice": 0}],
        "minPrice": 457,
        "rangePrice": {"MIN": 457, "MAX": 457},
        "title": "Насос Ручной",
        "brandName": "Без бренда",
        "available": True,
    }]
    products = _run_sync(offers)
    assert len(products) == 1
    assert products[0]["price_num"] == 457
    assert products[0]["price"] == "457 ₸"


def test_flat_price_zero_recovered_from_min_price_when_no_city_prices():
    offers = [{
        "masterSku": "1",
        "sku": "SKU-1",
        "price": 0,
        "availabilities": [{"available": "yes", "storeId": "20186986_001", "stockCount": 1}],
        "cityPrices": [],
        "minPrice": 999,
        "title": "Товар",
    }]
    products = _run_sync(offers)
    assert products[0]["price_num"] == 999


def test_flat_price_zero_recovered_from_range_price_as_last_resort():
    offers = [{
        "masterSku": "2",
        "sku": "SKU-2",
        "price": 0,
        "availabilities": [{"available": "yes", "storeId": "20186986_001", "stockCount": 1}],
        "rangePrice": {"MIN": 321, "MAX": 321},
        "title": "Товар 2",
    }]
    products = _run_sync(offers)
    assert products[0]["price_num"] == 321


def test_genuinely_priceless_offer_still_shows_not_specified():
    offers = [{
        "masterSku": "3",
        "sku": "SKU-3",
        "price": 0,
        "availabilities": [{"available": "yes", "storeId": "20186986_001", "stockCount": 1}],
        "title": "Товар без цены нигде",
    }]
    products = _run_sync(offers)
    assert products[0]["price_num"] == 0
    assert products[0]["price"] == "Цена не указана"


def test_normal_flat_price_still_used_directly():
    offers = [{
        "masterSku": "4",
        "sku": "SKU-4",
        "price": 1500,
        "availabilities": [{"available": "yes", "storeId": "20186986_001", "stockCount": 1}],
        "cityPrices": [{"value": 999999, "cityId": "750000000"}],
        "title": "Товар с корректной плоской ценой",
    }]
    products = _run_sync(offers)
    assert products[0]["price_num"] == 1500
