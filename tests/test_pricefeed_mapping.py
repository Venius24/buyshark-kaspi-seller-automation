import os
import sys
import unittest

sys.path.append(os.getcwd())

from backend.services import pricefeed_mapping


def _item(sku="X1", master="1000", algorithm="BEST_PRICE_MINUS_STEP", min_price=100, max_price=500, step=2, title="Товар"):
    return {
        "sku": sku,
        "masterSku": master,
        "masterTitle": title,
        "algorithm": algorithm,
        "minPrice": min_price,
        "maxPrice": max_price,
        "step": step,
    }


class TestMapItem(unittest.TestCase):
    def test_all_known_algorithms_map(self):
        expected = {
            "BEST_PRICE_MINUS_STEP": "TOP_1",
            "BEST_PRICE": "MATCH_FIRST",
            "NEXT_PRICE_MINUS_STEP": "TOP_2",
            "BEST_PRICE_PLUS_STEP": "TOP_2",
        }
        for algo, mode in expected.items():
            cfg, reason = pricefeed_mapping.map_item(_item(algorithm=algo))
            self.assertIsNone(reason, f"{algo} should not be skipped")
            self.assertEqual(cfg["mode"], mode)
            self.assertTrue(cfg["enabled"])

    def test_off_algorithm_skipped(self):
        cfg, reason = pricefeed_mapping.map_item(_item(algorithm="OFF"))
        self.assertIsNone(cfg)
        self.assertEqual(reason, "algorithm_off_no_limits")

    def test_unknown_algorithm_skipped_not_defaulted(self):
        cfg, reason = pricefeed_mapping.map_item(_item(algorithm="SOME_NEW_ALGO"))
        self.assertIsNone(cfg)
        self.assertEqual(reason, "unknown_algorithm")

    def test_null_min_price_skipped(self):
        cfg, reason = pricefeed_mapping.map_item(_item(min_price=None))
        self.assertIsNone(cfg)
        self.assertEqual(reason, "no_min_or_max")

    def test_zero_max_price_skipped(self):
        cfg, reason = pricefeed_mapping.map_item(_item(max_price=0))
        self.assertIsNone(cfg)
        self.assertEqual(reason, "no_min_or_max")

    def test_min_gte_max_skipped(self):
        cfg, reason = pricefeed_mapping.map_item(_item(min_price=500, max_price=500))
        self.assertIsNone(cfg)
        self.assertEqual(reason, "min_gte_max")

    def test_null_step_defaults_to_two(self):
        cfg, reason = pricefeed_mapping.map_item(_item(step=None))
        self.assertIsNone(reason)
        self.assertEqual(cfg["dump_step"], 2.0)

    def test_zero_step_defaults_to_two(self):
        cfg, reason = pricefeed_mapping.map_item(_item(step=0))
        self.assertIsNone(reason)
        self.assertEqual(cfg["dump_step"], 2.0)


class TestBuildSkuIndex(unittest.TestCase):
    def test_matches_on_all_four_fields_case_insensitive(self):
        products = [
            {"sku": "AbC-1", "merchantSku": "AbC-1"},
            {"sku": "P2", "masterSku": "9999"},
            {"sku": "P3", "kaspiSku": "kk-3"},
        ]
        index = pricefeed_mapping.build_sku_index(products)
        self.assertEqual(index["abc-1"], "AbC-1")
        self.assertEqual(index[" p2".strip().lower()], "P2")
        self.assertEqual(index["9999"], "P2")
        self.assertEqual(index["kk-3"], "P3")

    def test_prefers_merchant_sku_as_canonical(self):
        products = [{"sku": "internal", "merchantSku": "MS-1"}]
        index = pricefeed_mapping.build_sku_index(products)
        self.assertEqual(index["internal"], "MS-1")
        self.assertEqual(index["ms-1"], "MS-1")


class TestMatchItems(unittest.TestCase):
    def setUp(self):
        self.products = [{"sku": "X1", "merchantSku": "X1"}]

    def test_unmatched_sku_produces_no_row(self):
        items = [_item(sku="NOT-IN-CATALOG", master="0")]
        report = pricefeed_mapping.match_items(items, self.products, {})
        self.assertEqual(report.rows, {})
        self.assertIn("NOT-IN-CATALOG", report.unmatched)
        self.assertEqual(report.skipped.get("not_in_catalog"), 1)

    def test_matched_sku_produces_row(self):
        items = [_item(sku="X1")]
        report = pricefeed_mapping.match_items(items, self.products, {})
        self.assertIn("X1", report.rows)
        self.assertEqual(report.rows["X1"]["min_price"], 100)

    def test_already_configured_tracked(self):
        items = [_item(sku="X1")]
        existing = {"X1": {"min_price": 1, "max_price": 2}}
        report = pricefeed_mapping.match_items(items, self.products, existing)
        self.assertIn("X1", report.already_configured)

    def test_duplicate_sku_keeps_first_and_warns(self):
        items = [_item(sku="X1", min_price=100), _item(sku="X1", min_price=200)]
        report = pricefeed_mapping.match_items(items, self.products, {})
        self.assertEqual(report.rows["X1"]["min_price"], 100)
        self.assertEqual(report.skipped.get("duplicate_sku"), 1)
        self.assertTrue(any("Дубликат" in w for w in report.warnings))

    def test_unknown_algorithm_warns_with_count(self):
        items = [_item(sku="X1", algorithm="WEIRD_NEW_ONE")]
        report = pricefeed_mapping.match_items(items, self.products, {})
        self.assertEqual(report.rows, {})
        self.assertTrue(any("незнакомым алгоритмом" in w for w in report.warnings))

    def test_realistic_fixture_1084_items_358_importable(self):
        """Регрессия на реальные цифры живой выгрузки BARKHAN (2026-08-12):
        1084 товара, из них 358 с настроенным min/max (остальные algorithm=OFF)."""
        items = []
        products = []
        for i in range(1084):
            sku = f"SKU-{i}"
            products.append({"sku": sku, "merchantSku": sku})
            if i < 358:
                items.append(_item(sku=sku, algorithm="BEST_PRICE_MINUS_STEP", min_price=100 + i, max_price=1000 + i))
            else:
                items.append(_item(sku=sku, algorithm="OFF", min_price=None, max_price=None))
        report = pricefeed_mapping.match_items(items, products, {})
        self.assertEqual(len(report.rows), 358)
        self.assertEqual(report.skipped.get("algorithm_off_no_limits"), 1084 - 358)


class TestMergeWithExisting(unittest.TestCase):
    def test_overwrite_merge_preserves_fields_not_in_pricefeed(self):
        existing = {
            "min_price": 1, "max_price": 2, "dump_step": 1.0, "enabled": True, "mode": "TOP_1",
            "target_position": 3,
            "max_delivery_days": 5,
            "min_seller_rating": 4.5,
            "same_city_only": True,
            "blacklisted_merchants": ["m1", "m2"],
            "city_code": "119036852",
        }
        new_config, _ = pricefeed_mapping.map_item(_item(min_price=900, max_price=1500, step=3, algorithm="TOP_2" if False else "BEST_PRICE_MINUS_STEP"))
        merged = pricefeed_mapping.merge_with_existing(new_config, existing)

        # PriceFeed-controlled fields updated
        self.assertEqual(merged["min_price"], 900)
        self.assertEqual(merged["max_price"], 1500)
        self.assertEqual(merged["dump_step"], 3)
        self.assertEqual(merged["mode"], "TOP_1")

        # Fields with no PriceFeed source must survive untouched
        self.assertEqual(merged["target_position"], 3)
        self.assertEqual(merged["max_delivery_days"], 5)
        self.assertEqual(merged["min_seller_rating"], 4.5)
        self.assertEqual(merged["same_city_only"], True)
        self.assertEqual(merged["blacklisted_merchants"], ["m1", "m2"])
        self.assertEqual(merged["city_code"], "119036852")


if __name__ == "__main__":
    unittest.main()
