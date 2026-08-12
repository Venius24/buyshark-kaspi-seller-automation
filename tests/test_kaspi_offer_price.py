"""
Единый разбор цены оффера из ответа Kaspi BFF.

Плоское offer["price"] — устаревший кэш, у ~35% офферов реального каталога
отдающий 0 при назначенной цене. Правило было выучено в
kaspi_wrapper.sync_merchant_assortment (2026-07-29), но offer_sync читала поле
наивно и записала на Kaspi цену 0 — карточки сняли с продажи с причиной
"не указан в прайс-листе". Отсюда общий модуль и эти тесты.
"""
import os
import sys
import unittest

sys.path.append(os.getcwd())

from backend.kaspi_offer_price import extract_offer_price


class TestExtractOfferPrice(unittest.TestCase):
    def test_flat_price_wins_when_positive(self):
        self.assertEqual(extract_offer_price({"price": 1490, "minPrice": 990}), 1490)

    def test_falls_back_to_city_prices(self):
        self.assertEqual(
            extract_offer_price({"price": 0, "cityPrices": [{"cityId": "750000000", "value": 992}]}),
            992,
        )

    def test_skips_zero_city_price_entries(self):
        self.assertEqual(
            extract_offer_price({"price": 0, "cityPrices": [{"value": 0}, {"value": 988}]}),
            988,
        )

    def test_falls_back_to_min_price(self):
        self.assertEqual(extract_offer_price({"price": 0, "minPrice": 457}), 457)

    def test_falls_back_to_range_price(self):
        self.assertEqual(
            extract_offer_price({"price": 0, "rangePrice": {"MIN": 457, "MAX": 600}}), 457
        )

    def test_falls_back_to_all_city_prices(self):
        self.assertEqual(
            extract_offer_price({"price": 0, "allCityPrices": {"750000000": {"price": 321}}}), 321
        )

    def test_returns_none_not_zero_when_nothing_is_readable(self):
        # Ключевое различие: None означает "не знаем" и запускает повторную
        # сверку, а 0 — валидное число, которое уедет в payload и снимет
        # карточку с продажи.
        self.assertIsNone(extract_offer_price({"price": 0}))
        self.assertIsNone(extract_offer_price({}))
        self.assertIsNone(extract_offer_price(None))

    def test_ignores_negative_and_bool(self):
        self.assertIsNone(extract_offer_price({"price": -100}))
        self.assertIsNone(extract_offer_price({"price": True}))

    def test_survives_garbage_shapes(self):
        self.assertIsNone(extract_offer_price({"price": "1000", "cityPrices": "нет"}))
        self.assertEqual(extract_offer_price({"cityPrices": [None, {"value": 5}]}), 5)


if __name__ == "__main__":
    unittest.main()
