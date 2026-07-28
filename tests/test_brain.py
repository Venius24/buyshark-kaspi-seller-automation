import os
import sys
import unittest

sys.path.append(os.getcwd())

from backend.dumper.brain import DumpingEngine
from backend.dumper.config import DumpingProductConfig


def make_config(**overrides) -> DumpingProductConfig:
    defaults = dict(
        sku="TEST-0001",
        master_sku="123456",
        my_current_price=1000.0,
        min_price=0.0,
        max_price=0.0,
        step=10.0,
        enabled=True,
        mode="TOP_1",
        target_position=1,
        max_delivery_days=None,
        min_seller_rating=None,
        same_city_only=False,
        blacklisted_merchants=None,
        city_code="750000000",
    )
    defaults.update(overrides)
    return DumpingProductConfig(**defaults)


def make_offer(merchant_id, price, **overrides):
    offer = {"merchantId": merchant_id, "merchantName": f"Seller {merchant_id}", "price": price}
    offer.update(overrides)
    return offer


class TestFilterOffers(unittest.TestCase):
    """Тесты DumpingEngine.filter_offers — фильтрация предложений конкурентов."""

    def test_empty_offers_returns_empty(self):
        config = make_config()
        self.assertEqual(DumpingEngine.filter_offers(config, "999", []), [])

    def test_blacklist_by_merchant_id(self):
        config = make_config(blacklisted_merchants=["111"])
        offers = [make_offer("111", 900), make_offer("222", 950)]
        cleaned = DumpingEngine.filter_offers(config, "999", offers)
        self.assertEqual([o["merchantId"] for o in cleaned], ["222"])

    def test_blacklist_by_merchant_name(self):
        config = make_config(blacklisted_merchants=["Seller 111"])
        offers = [make_offer("111", 900), make_offer("222", 950)]
        cleaned = DumpingEngine.filter_offers(config, "999", offers)
        self.assertEqual([o["merchantId"] for o in cleaned], ["222"])

    def test_price_parsing_with_currency_symbol_and_spaces(self):
        config = make_config()
        offers = [{"merchantId": "111", "merchantName": "S", "price": "1 234 ₸"}]
        cleaned = DumpingEngine.filter_offers(config, "999", offers)
        self.assertEqual(cleaned[0]["price"], 1234.0)

    def test_missing_price_is_skipped(self):
        config = make_config()
        offers = [{"merchantId": "111", "merchantName": "S"}]
        self.assertEqual(DumpingEngine.filter_offers(config, "999", offers), [])

    def test_malformed_price_is_skipped(self):
        config = make_config()
        offers = [make_offer("111", "not-a-price")]
        self.assertEqual(DumpingEngine.filter_offers(config, "999", offers), [])

    def test_min_seller_rating_filter(self):
        config = make_config(min_seller_rating=4.5)
        offers = [
            make_offer("111", 900, rating=4.0),
            make_offer("222", 950, rating=4.8),
            make_offer("333", 960),  # no rating -> not filtered
        ]
        cleaned = DumpingEngine.filter_offers(config, "999", offers)
        self.assertEqual({o["merchantId"] for o in cleaned}, {"222", "333"})

    def test_max_delivery_days_filter(self):
        config = make_config(max_delivery_days=3)
        offers = [
            make_offer("111", 900, deliveryDays=5),
            make_offer("222", 950, deliveryDays=2),
            make_offer("333", 960),  # no delivery info -> not filtered
        ]
        cleaned = DumpingEngine.filter_offers(config, "999", offers)
        self.assertEqual({o["merchantId"] for o in cleaned}, {"222", "333"})

    def test_same_city_only_filter(self):
        config = make_config(same_city_only=True, city_code="750000000")
        offers = [
            make_offer("111", 900, cityCode="750000000"),
            make_offer("222", 950, cityCode="710000000"),
            make_offer("333", 960),  # no city info -> not filtered
        ]
        cleaned = DumpingEngine.filter_offers(config, "999", offers)
        self.assertEqual({o["merchantId"] for o in cleaned}, {"111", "333"})


class TestCalculateNewPriceOffMode(unittest.TestCase):
    def test_mode_off_returns_current_price(self):
        config = make_config(mode="OFF")
        res = DumpingEngine.calculate_new_price(config, "999", [make_offer("111", 900)])
        self.assertEqual(res["targetPrice"], config.my_current_price)
        self.assertEqual(res["action"], "DISABLED")
        self.assertEqual(res["dumping_status"], "OFF")

    def test_disabled_returns_current_price(self):
        config = make_config(enabled=False)
        res = DumpingEngine.calculate_new_price(config, "999", [make_offer("111", 900)])
        self.assertEqual(res["action"], "DISABLED")


class TestCalculateNewPriceNoCompetitors(unittest.TestCase):
    def test_no_offers_at_all_holds_position(self):
        config = make_config()
        res = DumpingEngine.calculate_new_price(config, "999", [])
        self.assertEqual(res["action"], "HOLD_POSITION_NO_DATA")
        self.assertEqual(res["targetPrice"], config.my_current_price)

    def test_offers_present_but_all_filtered_out_uses_max_price(self):
        config = make_config(max_price=1500.0, blacklisted_merchants=["111"])
        res = DumpingEngine.calculate_new_price(config, "999", [make_offer("111", 900)])
        self.assertEqual(res["action"], "SET_MAX_PRICE_SINGLE_SELLER")
        self.assertEqual(res["targetPrice"], 1500.0)

    def test_offers_present_but_all_filtered_out_no_max_price_keeps_current(self):
        config = make_config(max_price=0.0, blacklisted_merchants=["111"])
        res = DumpingEngine.calculate_new_price(config, "999", [make_offer("111", 900)])
        self.assertEqual(res["action"], "SET_MAX_PRICE_SINGLE_SELLER")
        self.assertEqual(res["targetPrice"], config.my_current_price)


class TestCalculateNewPriceTop1(unittest.TestCase):
    def test_dumps_below_best_competitor(self):
        config = make_config(mode="TOP_1", step=10.0, my_current_price=1000.0)
        offers = [make_offer("111", 950), make_offer("222", 980)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "DUMP_TOP_1")
        self.assertEqual(res["targetPrice"], 940.0)  # 950 - 10
        self.assertEqual(res["bestCompetitorPrice"], 950.0)

    def test_pulls_up_price_when_already_winning_with_large_gap(self):
        config = make_config(mode="TOP_1", step=10.0, my_current_price=500.0)
        offers = [make_offer("999", 500), make_offer("111", 900)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "PULL_UP_PRICE")
        self.assertEqual(res["targetPrice"], 890.0)  # 900 - 10

    def test_holds_when_already_winning_with_small_gap(self):
        config = make_config(mode="TOP_1", step=10.0, my_current_price=895.0)
        offers = [make_offer("999", 895), make_offer("111", 900)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "DUMP_TOP_1")
        self.assertEqual(res["targetPrice"], 890.0)


class TestCalculateNewPriceMatchFirst(unittest.TestCase):
    def test_matches_one_below_leader(self):
        config = make_config(mode="MATCH_FIRST", my_current_price=1000.0)
        offers = [make_offer("111", 950)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "MATCH_FIRST")
        self.assertEqual(res["targetPrice"], 949.0)

    def test_pulls_up_when_winning_with_gap(self):
        config = make_config(mode="MATCH_FIRST", my_current_price=500.0)
        offers = [make_offer("999", 500), make_offer("111", 900)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "PULL_UP_MATCH_FIRST")
        self.assertEqual(res["targetPrice"], 899.0)


class TestCalculateNewPriceTop2(unittest.TestCase):
    def test_holds_second_place_below_second_competitor(self):
        config = make_config(mode="TOP_2", step=10.0, my_current_price=1000.0)
        offers = [make_offer("111", 900), make_offer("222", 950)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "HOLD_TOP_2")
        self.assertEqual(res["targetPrice"], 940.0)  # 950 - 10

    def test_does_not_overtake_first_place(self):
        config = make_config(mode="TOP_2", step=100.0, my_current_price=1000.0)
        offers = [make_offer("111", 900), make_offer("222", 920)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        # 920 - 100 = 820, which would be <= best_competitor_price(900) -> clamp to 901
        self.assertEqual(res["targetPrice"], 901.0)

    def test_single_competitor_takes_second_place_above(self):
        config = make_config(mode="TOP_2", step=10.0, my_current_price=1000.0)
        offers = [make_offer("111", 900)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["targetPrice"], 910.0)  # 900 + step


class TestCalculateNewPriceTargetPosition(unittest.TestCase):
    def test_target_position_1_behaves_like_top1(self):
        config = make_config(mode="TARGET_POSITION", target_position=1, step=10.0, my_current_price=1000.0)
        offers = [make_offer("111", 900)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "TARGET_POS_1")
        self.assertEqual(res["targetPrice"], 890.0)

    def test_target_position_n_with_enough_competitors(self):
        config = make_config(mode="TARGET_POSITION", target_position=3, step=10.0, my_current_price=1000.0)
        offers = [make_offer("111", 800), make_offer("222", 850), make_offer("333", 900)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "TARGET_POS_3")
        self.assertEqual(res["targetPrice"], 890.0)  # 900 - 10, doesn't undercut prev (850)

    def test_target_position_n_would_undercut_previous_place_clamped(self):
        config = make_config(mode="TARGET_POSITION", target_position=3, step=100.0, my_current_price=1000.0)
        offers = [make_offer("111", 800), make_offer("222", 850), make_offer("333", 900)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        # 900 - 100 = 800 <= prev(850) -> clamp to 851
        self.assertEqual(res["targetPrice"], 851.0)

    def test_target_position_n_with_fewer_competitors_than_n(self):
        config = make_config(mode="TARGET_POSITION", target_position=5, step=10.0, my_current_price=1000.0)
        offers = [make_offer("111", 800), make_offer("222", 850)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "TARGET_POS_5_AFTER_LAST")
        self.assertEqual(res["targetPrice"], 860.0)  # last competitor + step


class TestCalculateNewPriceMinMaxClamp(unittest.TestCase):
    def test_min_price_clamp(self):
        config = make_config(mode="TOP_1", step=10.0, min_price=800.0, my_current_price=1000.0)
        offers = [make_offer("111", 700)]  # target would be 690, below min_price
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["action"], "REACHED_MIN_PRICE")
        self.assertEqual(res["dumping_status"], "AT_MIN_PRICE")
        self.assertEqual(res["targetPrice"], 800.0)

    def test_max_price_clamp(self):
        config = make_config(mode="TOP_2", step=10.0, max_price=920.0, my_current_price=1000.0)
        offers = [make_offer("111", 900)]  # single competitor -> 900 + 10 = 910, under max, so raise max lower for test
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["targetPrice"], 910.0)

    def test_max_price_clamp_actually_triggers(self):
        config = make_config(mode="TOP_2", step=50.0, max_price=920.0, my_current_price=1000.0)
        offers = [make_offer("111", 900)]  # single competitor -> 900 + 50 = 950, above max
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["targetPrice"], 920.0)

    def test_dumping_status_labels_by_expected_position(self):
        config = make_config(mode="TOP_2", step=10.0, my_current_price=1000.0)
        offers = [make_offer("111", 900), make_offer("222", 950)]
        res = DumpingEngine.calculate_new_price(config, "999", offers)
        self.assertEqual(res["dumping_status"], "HOLDING_2ND")


if __name__ == "__main__":
    unittest.main()
