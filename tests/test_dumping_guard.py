"""Защита демпинга от оттеснения: backend/dumper/guard.py + встраивание в brain.py.

Сценарии взяты с живых данных Бархана (сентябрь 2026, HANDOFF §3.111).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.append(os.getcwd())

from backend.dumper import guard
from backend.dumper.brain import BrainWorker, DumpingEngine
from backend.dumper.config import DumpingProductConfig, build_effective_dumping_configs
from backend.dumper.guard_backfill import parse_log_message
from backend.services import dumping_attacks, dumping_filters

ME = "20186986"
NOW = datetime(2026, 10, 3, 12, 0, 0)


def kaspi_offer(mid, price, name=None, rating=None, reviews=None, intercity=False, days=1, preorder=0):
    """Оффер в форме настоящего ответа Kaspi offer-view."""
    o = {
        "merchantId": mid,
        "merchantName": name or f"Seller {mid}",
        "price": float(price),
        "preorder": preorder,
        "deliveryOptions": {
            "POSTOMAT": {"deliveryDays": days, "interCity": intercity, "deliveryType": "POSTOMAT"},
            "TO_DOOR": {"deliveryDays": days, "interCity": intercity, "deliveryType": "TO_DOOR"},
        },
    }
    if rating is not None:
        o["merchantRating"] = rating
    if reviews is not None:
        o["merchantReviewsQuantity"] = reviews
    return o


def me(price, days=1):
    return kaspi_offer(ME, price, "BARKHAN", 4.8, 2156, False, days)


def cfg(price, **kw):
    base = dict(sku="X974-0001-2", master_sku="141535749", my_current_price=float(price),
                min_price=326.0, max_price=890.0, step=2.0, mode="TOP_1")
    base.update(kw)
    return DumpingProductConfig(**base)


# Механику лесенки проверяем на коротких сериях — пороги до 05.10.2026.
OLD_LADDER = {"ladder_min_moves": 5, "ladder_min_slide_pct": None}


def ladder_moves(start, n, rival_id="20437934", every_min=15, t0=None, undercut=1):
    """Наши ответы на подрезку по −1: мы X, соперник X−1, мы X−3 ..."""
    t0 = t0 or NOW - timedelta(minutes=every_min * n)
    out, price = [], float(start)
    for i in range(n):
        rival = price - undercut
        new = rival - 2
        out.append({"created_at": t0 + timedelta(minutes=every_min * i), "our_old": price,
                    "our_new": new, "rival_id": rival_id, "rival_name": "ИП КАРАБАЕВА",
                    "rival_price": rival})
        price = new
    return out


class TestMerchantAge(unittest.TestCase):
    def test_anchor_dates(self):
        # ИП КАРАБАЕВА — опорная точка, зарегистрирована 2025-12-22.
        self.assertEqual(guard.estimate_registered_at(20437934).date().isoformat(), "2025-12-22")
        self.assertEqual(guard.merchant_age_days(20437934, NOW), (NOW - datetime(2025, 12, 22)).days)

    def test_interpolation_between_anchors(self):
        reg = guard.estimate_registered_at(20460000)
        self.assertTrue(datetime(2026, 2, 28) < reg < datetime(2026, 7, 5))

    def test_extrapolation_after_last_anchor(self):
        reg = guard.estimate_registered_at(20482000)
        self.assertGreater(reg, datetime(2026, 9, 28))

    def test_old_or_non_numeric_unknown(self):
        self.assertIsNone(guard.merchant_age_days("Astanacase", NOW))
        self.assertIsNone(guard.merchant_age_days(17508765, NOW))

    def test_out_of_series_id_does_not_overflow(self):
        # Живой номер продавца с карточки Бархана 03.10 — раньше ронял цикл OverflowError.
        self.assertIsNone(guard.merchant_age_days("2207134711", NOW))


class TestNormalizeOffer(unittest.TestCase):
    def test_real_kaspi_fields(self):
        n = guard.normalize_offer(kaspi_offer("20437934", 435, rating=5.0, reviews=24, intercity=True, days=3))
        self.assertEqual(n["rating"], 5.0)
        self.assertEqual(n["reviews"], 24)
        self.assertTrue(n["intercity"])
        self.assertEqual(n["delivery_days"], 3)

    def test_missing_rating_is_none_not_zero(self):
        n = guard.normalize_offer(kaspi_offer("20460997", 440))
        self.assertIsNone(n["rating"])
        self.assertIsNone(n["reviews"])

    def test_intercity_only_if_all_options_intercity(self):
        o = kaspi_offer("1", 100, intercity=True)
        o["deliveryOptions"]["TO_DOOR"]["interCity"] = False
        self.assertFalse(guard.normalize_offer(o)["intercity"])


class TestLegacyFiltersNowWork(unittest.TestCase):
    """До 2026-10-03 эти фильтры читали несуществующие поля и не отсеивали никого."""

    def test_min_seller_rating_reads_merchant_rating(self):
        offers = [me(433), kaspi_offer("1", 430, rating=4.0, reviews=50), kaspi_offer("2", 440, rating=4.9, reviews=50)]
        cleaned = DumpingEngine.filter_offers(cfg(433, min_seller_rating=4.5), ME, offers)
        self.assertEqual([o["merchantId"] for o in cleaned], [ME, "2"])

    def test_same_city_reads_intercity(self):
        offers = [me(433), kaspi_offer("1", 430, intercity=True), kaspi_offer("2", 440)]
        cleaned = DumpingEngine.filter_offers(cfg(433, same_city_only=True), ME, offers)
        self.assertEqual([o["merchantId"] for o in cleaned], [ME, "2"])

    def test_max_delivery_days_reads_delivery_options(self):
        offers = [me(433, days=5), kaspi_offer("1", 430, days=10), kaspi_offer("2", 440, days=2)]
        cleaned = DumpingEngine.filter_offers(cfg(433, max_delivery_days=3), ME, offers)
        # Наш оффер не фильтруется никогда, даже если сам «медленный».
        self.assertEqual([o["merchantId"] for o in cleaned], [ME, "2"])


class TestWeakReasons(unittest.TestCase):
    def reasons(self, off, **kw):
        return guard.weak_reasons(guard.normalize_offer(off), guard.GuardConfig(enabled=True, **kw),
                                  guard.normalize_offer(me(433)), NOW)

    def test_few_reviews(self):
        self.assertIn(guard.REASON_FEW_REVIEWS, self.reasons(kaspi_offer("1", 1, rating=5.0, reviews=24), min_reviews=30))
        self.assertEqual(self.reasons(kaspi_offer("1", 1, rating=5.0, reviews=301), min_reviews=30), [])

    def test_no_rating_is_not_cut_by_review_threshold(self):
        # Решение владельца 03.10: продавца без рейтинга решает менеджер,
        # порог отзывов к нему не применяется.
        self.assertEqual(self.reasons(kaspi_offer("1", 1), min_reviews=50), [])
        self.assertIn(guard.REASON_NO_RATING,
                      self.reasons(kaspi_offer("1", 1, rating=0.0, reviews=0), no_rating_action="IGNORE"))

    def test_legacy_ignore_no_rating_flag(self):
        self.assertEqual(guard.GuardConfig.from_dict({"ignore_no_rating": True}).no_rating_action, "IGNORE")
        self.assertEqual(guard.GuardConfig.from_dict({"ignore_no_rating": False}).no_rating_action, "CONSIDER")

    def test_manager_decision(self):
        off = guard.normalize_offer(kaspi_offer("7", 1, rating=5.0, reviews=2))
        g = guard.GuardConfig(enabled=True, min_reviews=50)
        self.assertEqual(guard.weak_reasons(off, g, None, NOW, decision="CONSIDER"), [])
        self.assertIn(guard.REASON_MANAGER, guard.weak_reasons(guard.normalize_offer(kaspi_offer("7", 1)), g, None, NOW,
                                                              decision="IGNORE"))

    def test_needs_decision(self):
        g = guard.GuardConfig(enabled=True)
        no_rating = guard.normalize_offer(kaspi_offer("7", 1))
        self.assertTrue(guard.needs_decision(no_rating, g))
        self.assertFalse(guard.needs_decision(no_rating, g, "IGNORE"))
        self.assertFalse(guard.needs_decision(guard.normalize_offer(kaspi_offer("7", 1, rating=4.0, reviews=3)), g))
        self.assertFalse(guard.needs_decision(no_rating, guard.GuardConfig(enabled=True, no_rating_action="CONSIDER")))

    def test_newcomer(self):
        self.assertIn(guard.REASON_NEWCOMER, self.reasons(kaspi_offer("20475029", 1), min_merchant_age_days=180))
        self.assertEqual(self.reasons(kaspi_offer("17606197", 1), min_merchant_age_days=180), [])

    def test_slow_delivery_relative_to_ours(self):
        self.assertIn(guard.REASON_SLOW_DELIVERY, self.reasons(kaspi_offer("1", 1, days=4), max_extra_delivery_days=2))
        self.assertEqual(self.reasons(kaspi_offer("1", 1, days=3), max_extra_delivery_days=2), [])

    def test_whitelist_beats_everything(self):
        off = kaspi_offer("20437934", 1, name="ИП КАРАБАЕВА", rating=5.0, reviews=2)
        self.assertEqual(self.reasons(off, min_reviews=30, whitelist=["ИП карабаева"]), [])

    def test_blacklist_by_name_case_insensitive(self):
        self.assertIn(guard.REASON_BLACKLIST, self.reasons(kaspi_offer("9", 1, name="Album"), blacklist=["album"]))


class TestEngineWithGuard(unittest.TestCase):
    def test_weak_rival_ignored_we_price_against_next(self):
        # Живой расклад подставки 03.10: мы 433, КАРАБАЕВА 435 (24 отзыва),
        # дальше новички без рейтинга, первый нормальный — ИП БЕРЕКЕ? нет, 21 отзыв;
        # Mix&More 1200 (36 отзывов).
        offers = [me(433), kaspi_offer("20437934", 435, rating=5.0, reviews=24, intercity=True, days=3),
                  kaspi_offer("20460997", 440), kaspi_offer("20443832", 639, rating=4.9, reviews=21),
                  kaspi_offer("20431105", 1200, rating=4.9, reviews=36)]
        g = guard.GuardConfig(enabled=True, min_reviews=30, no_rating_action="IGNORE")
        res = DumpingEngine.calculate_new_price(cfg(433), ME, offers, guard_cfg=g, guard_ctx={"now": NOW})
        # Мы на 1-м месте среди учитываемых, разрыв до Mix&More большой → подтяжка.
        self.assertEqual(res["action"], "PULL_UP_PRICE")
        self.assertEqual(res["targetPrice"], 890.0)  # упёрлись в max_price
        self.assertEqual(len(res["guard"]["ignored"]), 3)
        self.assertEqual(res["rival"]["merchant_id"], "20431105")

    def test_without_guard_behaviour_unchanged(self):
        offers = [me(433), kaspi_offer("20437934", 431, rating=5.0, reviews=24)]
        res = DumpingEngine.calculate_new_price(cfg(433), ME, offers)
        self.assertEqual(res["targetPrice"], 429.0)
        self.assertEqual(res["guard"]["ignored"], [])

    def test_all_ignored_holds_price_instead_of_max(self):
        offers = [me(578), kaspi_offer("20450526", 576, rating=0.0, reviews=0)]
        g = guard.GuardConfig(enabled=True, min_reviews=30, no_rating_action="IGNORE")
        res = DumpingEngine.calculate_new_price(cfg(578), ME, offers, guard_cfg=g, guard_ctx={"now": NOW})
        self.assertEqual(res["action"], "GUARD_ALL_IGNORED_HOLD")
        self.assertEqual(res["targetPrice"], 578)

    def test_all_ignored_max_option(self):
        offers = [me(578), kaspi_offer("20450526", 576, rating=0.0, reviews=0)]
        g = guard.GuardConfig(enabled=True, min_reviews=30, all_ignored_action="MAX", no_rating_action="IGNORE")
        res = DumpingEngine.calculate_new_price(cfg(578), ME, offers, guard_cfg=g, guard_ctx={"now": NOW})
        self.assertEqual(res["action"], "SET_MAX_PRICE_SINGLE_SELLER")
        self.assertEqual(res["targetPrice"], 890.0)

    def test_floor_below_next_competitor(self):
        # X172-0009-1: Ракатова 134, следующий — Daulet 212. Без защиты мы 132.
        offers = [me(135), kaspi_offer("20349688", 134, rating=4.7, reviews=1190),
                  kaspi_offer("20294382", 212, rating=4.5, reviews=150)]
        c = cfg(135, min_price=100.0, max_price=500.0)
        g = guard.GuardConfig(enabled=True, floor_below_next_pct=15)
        res = DumpingEngine.calculate_new_price(c, ME, offers, guard_cfg=g, guard_ctx={"now": NOW})
        self.assertEqual(res["targetPrice"], 181.0)  # ceil(212 * 0.85)
        self.assertEqual(res["action"], "GUARD_FLOOR_NEXT")
        self.assertEqual(res["dumping_status"], "GUARD_HOLD")

    def test_floor_not_applied_when_best_is_close_to_next(self):
        offers = [me(500), kaspi_offer("1", 498, rating=4.7, reviews=500), kaspi_offer("2", 510, rating=4.7, reviews=500)]
        g = guard.GuardConfig(enabled=True, floor_below_next_pct=15)
        res = DumpingEngine.calculate_new_price(cfg(500, max_price=900.0), ME, offers, guard_cfg=g, guard_ctx={"now": NOW})
        self.assertEqual(res["targetPrice"], 496.0)
        self.assertIsNone(res["guard"]["limit"])

    def test_floor_never_above_max_price(self):
        offers = [me(135), kaspi_offer("1", 134, rating=4.7, reviews=500), kaspi_offer("2", 900, rating=4.7, reviews=500)]
        g = guard.GuardConfig(enabled=True, floor_below_next_pct=10)
        res = DumpingEngine.calculate_new_price(cfg(135, min_price=100.0, max_price=300.0), ME, offers,
                                                guard_cfg=g, guard_ctx={"now": NOW})
        self.assertEqual(res["targetPrice"], 300.0)

    def test_daily_drop_cap(self):
        offers = [me(500), kaspi_offer("1", 400, rating=4.7, reviews=500)]
        g = guard.GuardConfig(enabled=True, max_daily_drop_pct=10)
        res = DumpingEngine.calculate_new_price(cfg(500, max_price=900.0), ME, offers, guard_cfg=g,
                                                guard_ctx={"now": NOW, "peak_24h": 520.0})
        self.assertEqual(res["targetPrice"], 468.0)  # ceil(520 * 0.9)
        self.assertEqual(res["action"], "GUARD_DAILY_DROP")

    def test_min_price_still_wins_over_guard(self):
        offers = [me(330), kaspi_offer("1", 300, rating=4.7, reviews=500)]
        g = guard.GuardConfig(enabled=True, max_daily_drop_pct=50)
        res = DumpingEngine.calculate_new_price(cfg(330), ME, offers, guard_cfg=g,
                                                guard_ctx={"now": NOW, "peak_24h": 340.0})
        self.assertEqual(res["targetPrice"], 326.0)
        self.assertEqual(res["action"], "REACHED_MIN_PRICE")


class TestLadder(unittest.TestCase):
    def test_detects_minus_one_ladder(self):
        g = guard.GuardConfig(enabled=True, **OLD_LADDER)
        sig = guard.detect_ladder(ladder_moves(603, 8), g, step=2.0, now=NOW)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.rival_id, "20437934")
        self.assertEqual(sig.moves, 8)
        self.assertEqual(sig.price_from, 603)

    def test_too_few_moves_is_not_ladder(self):
        g = guard.GuardConfig(enabled=True, ladder_min_moves=5)
        self.assertIsNone(guard.detect_ladder(ladder_moves(603, 4), g, step=2.0, now=NOW))

    def test_big_undercut_is_a_price_decision_not_a_bot(self):
        g = guard.GuardConfig(enabled=True)
        self.assertIsNone(guard.detect_ladder(ladder_moves(603, 8, undercut=50), g, step=2.0, now=NOW))

    def test_moves_spread_over_days_are_not_ladder(self):
        g = guard.GuardConfig(enabled=True, ladder_window_hours=24, ladder_cooldown_hours=0)
        self.assertIsNone(guard.detect_ladder(
            ladder_moves(603, 6, every_min=60 * 10, t0=NOW - timedelta(hours=60)), g, step=2.0, now=NOW))

    def test_cooldown_expires(self):
        g = guard.GuardConfig(enabled=True, ladder_cooldown_hours=12, **OLD_LADDER)
        moves = ladder_moves(603, 8, t0=NOW - timedelta(hours=20))
        sig = guard.detect_ladder(moves, g, step=2.0, now=NOW)
        self.assertIsNotNone(sig)
        self.assertFalse(guard.ladder_is_active(sig, NOW))
        self.assertTrue(guard.ladder_is_active(sig, moves[-1]["created_at"] + timedelta(hours=1)))

    def test_freeze_holds_price(self):
        offers = [me(587), kaspi_offer("20437934", 586, rating=5.0, reviews=24),
                  kaspi_offer("2", 700, rating=4.8, reviews=400)]
        g = guard.GuardConfig(enabled=True, ladder_response="FREEZE", min_reviews=None, **OLD_LADDER)
        res = DumpingEngine.calculate_new_price(cfg(587), ME, offers, guard_cfg=g,
                                                guard_ctx={"now": NOW, "moves": ladder_moves(620, 6)})
        self.assertTrue(res["guard"]["ladder_active"])
        self.assertEqual(res["targetPrice"], 587)
        self.assertEqual(res["action"], "GUARD_LADDER_FREEZE")

    def test_retreat_stops_answering_that_rival(self):
        offers = [me(587), kaspi_offer("20437934", 586, rating=5.0, reviews=240),
                  kaspi_offer("2", 700, rating=4.8, reviews=400)]
        g = guard.GuardConfig(enabled=True, ladder_response="RETREAT", **OLD_LADDER)
        res = DumpingEngine.calculate_new_price(cfg(587), ME, offers, guard_cfg=g,
                                                guard_ctx={"now": NOW, "moves": ladder_moves(620, 6)})
        self.assertEqual(res["targetPrice"], 698.0)  # встаём под следующего
        self.assertEqual(res["guard"]["ignored"][0]["reasons"], [guard.REASON_LADDER_RETREAT])

    def test_flag_only_reports(self):
        offers = [me(587), kaspi_offer("20437934", 586, rating=5.0, reviews=240)]
        g = guard.GuardConfig(enabled=True, ladder_response="FLAG", **OLD_LADDER)
        res = DumpingEngine.calculate_new_price(cfg(587), ME, offers, guard_cfg=g,
                                                guard_ctx={"now": NOW, "moves": ladder_moves(620, 6)})
        self.assertEqual(res["targetPrice"], 584.0)
        self.assertIsNotNone(res["guard"]["ladder"])


class TestResolveConfig(unittest.TestCase):
    def test_inheritance_chain(self):
        store = {"enabled": True, "min_reviews": 30, "floor_below_next_pct": 15}
        group = {"mode": "CUSTOM", "min_reviews": 10}
        sku = {"mode": "CUSTOM", "floor_below_next_pct": 5}
        c = guard.resolve_guard_config(store, group, sku)
        self.assertTrue(c.enabled)
        self.assertEqual(c.min_reviews, 10)
        self.assertEqual(c.floor_below_next_pct, 5)

    def test_inherit_and_none_change_nothing(self):
        c = guard.resolve_guard_config({"enabled": True, "min_reviews": 30}, None, {"mode": "INHERIT"})
        self.assertEqual(c.min_reviews, 30)

    def test_off_disables(self):
        c = guard.resolve_guard_config({"enabled": True, "min_reviews": 30}, None, {"mode": "OFF"})
        self.assertFalse(c.enabled)

    def test_custom_on_store_without_defaults_enables(self):
        c = guard.resolve_guard_config({}, None, {"mode": "CUSTOM", "min_reviews": 5})
        self.assertTrue(c.enabled)

    def test_custom_can_clear_threshold(self):
        c = guard.resolve_guard_config({"enabled": True, "min_reviews": 30}, {"mode": "CUSTOM", "min_reviews": None})
        self.assertIsNone(c.min_reviews)

    def test_clean_override_validation(self):
        self.assertIsNone(guard.clean_guard_override({"mode": "inherit", "min_reviews": 3}))
        self.assertEqual(guard.clean_guard_override({"mode": "off"}), {"mode": "OFF"})
        self.assertEqual(guard.clean_guard_override({"mode": "CUSTOM", "min_reviews": "30"}),
                         {"mode": "CUSTOM", "min_reviews": 30})
        with self.assertRaises(guard.GuardValidationError):
            guard.clean_guard_override({"mode": "CUSTOM", "floor_below_next_pct": 95})
        with self.assertRaises(guard.GuardValidationError):
            guard.clean_guard_override({"mode": "CUSTOM", "ladder_response": "NUKE"})

    def test_group_guard_reaches_group_and_individual_skus(self):
        products = [{"merchantSku": "A"}, {"merchantSku": "B"}]
        configs = {"B": {"enabled": True, "min_price": 1, "max_price": 2}}
        groups = {"g": {"is_active": True, "linked_skus": ["A", "B"],
                        "dumping_config": {"enabled": True, "min_price": 5, "max_price": 9,
                                           "guard": {"mode": "CUSTOM", "min_reviews": 7}}}}
        eff, _ = build_effective_dumping_configs(products, configs, groups)
        self.assertEqual(eff["A"]["group_guard"]["min_reviews"], 7)
        self.assertNotIn("guard", eff["A"])
        self.assertEqual(eff["B"]["group_guard"]["min_reviews"], 7)


def snapshot(sku, price, offers, master="1"):
    return {"master_sku": master, "city_code": "750000000", "offers": offers,
            "product_data": {"price_num": price, "name": sku, "merchantSku": sku}}


class TestBrainWorkerSignals(unittest.TestCase):
    def run_brain(self, market, configs=None, guard_data=None):
        configs = configs or {sku: {"enabled": True, "min_price": 100, "max_price": 2000, "dump_step": 2}
                              for sku in market}
        return BrainWorker.calculate_price_decisions(market, configs, ME, guard_data={"now": NOW, **(guard_data or {})})

    def test_weak_rival_and_farm_and_card_rank(self):
        market = {"S1": snapshot("S1", 433, [
            me(433), kaspi_offer("20437934", 431, rating=5.0, reviews=24),
            kaspi_offer("20460997", 436), kaspi_offer("2", 1200, rating=4.9, reviews=900)])}
        res = self.run_brain(market)
        item = res["price_war_items"][0]
        self.assertIn("weak_rival", item["guard"]["signals"])
        self.assertIn("farm", item["guard"]["signals"])
        self.assertEqual(item["our_rank"], 2)

    def test_shadow_across_skus(self):
        market = {
            "S1": snapshot("S1", 433, [me(433), kaspi_offer("20437934", 435, rating=5.0, reviews=24),
                                       kaspi_offer("9", 432, rating=4.9, reviews=900)]),
            "S2": snapshot("S2", 638, [me(638), kaspi_offer("20437934", 640, rating=5.0, reviews=24),
                                       kaspi_offer("9", 637, rating=4.9, reviews=900)]),
            "S3": snapshot("S3", 500, [me(500), kaspi_offer("7", 900, rating=5.0, reviews=240)]),
            # Нормальный продавец «9» рядом с нами на S1, S2 и S4 — это просто сосед по нише, не тень.
            "S4": snapshot("S4", 700, [me(700), kaspi_offer("9", 699, rating=4.9, reviews=900)]),
        }
        res = self.run_brain(market)
        by = {it["sku"]: it for it in res["price_war_items"]}
        self.assertIn("shadow", by["S1"]["guard"]["signals"])
        self.assertIn("shadow", by["S2"]["guard"]["signals"])
        self.assertNotIn("shadow", by["S3"]["guard"]["signals"])
        self.assertNotIn("shadow", by["S4"]["guard"]["signals"])

    def test_store_defaults_and_sku_override_applied(self):
        offers = [me(433), kaspi_offer("20437934", 431, rating=5.0, reviews=24),
                  kaspi_offer("2", 600, rating=4.9, reviews=900)]
        market = {"S1": snapshot("S1", 433, offers), "S2": snapshot("S2", 433, offers)}
        configs = {
            "S1": {"enabled": True, "min_price": 100, "max_price": 2000, "dump_step": 2},
            "S2": {"enabled": True, "min_price": 100, "max_price": 2000, "dump_step": 2, "guard": {"mode": "OFF"}},
        }
        res = self.run_brain(market, configs, {"store_defaults": {"enabled": True, "min_reviews": 30}})
        new = {t["sku"]: t["new_price"] for t in res["update_queue"]}
        self.assertEqual(new["S1"], 598.0)   # КАРАБАЕВА отсеяна → встаём под 600
        self.assertEqual(new["S2"], 429.0)   # защита выключена на товаре → старое поведение

    def test_no_rating_rival_asked_not_ignored(self):
        offers = [me(433), kaspi_offer("20460997", 431, name="Пустышка"), kaspi_offer("2", 600, rating=4.9, reviews=900)]
        res = self.run_brain({"S1": snapshot("S1", 433, offers)}, guard_data={"store_defaults": {"enabled": True}})
        # Пока менеджер не решил — демпинг его учитывает (встаём под 431) и спрашивает.
        self.assertEqual(res["update_queue"][0]["new_price"], 429.0)
        self.assertEqual([a["merchant_id"] for a in res["guard_asks"]], ["20460997"])
        self.assertEqual(res["guard_asks"][0]["sku"], "S1")

    def test_manager_ignore_on_this_sku_only(self):
        offers = [me(433), kaspi_offer("20460997", 431), kaspi_offer("2", 600, rating=4.9, reviews=900)]
        market = {"S1": snapshot("S1", 433, offers), "S2": snapshot("S2", 433, offers)}
        res = self.run_brain(market, guard_data={"store_defaults": {"enabled": True},
                                                 "decisions_by_sku": {"S1": {"20460997": "IGNORE"}}})
        new = {t["sku"]: t["new_price"] for t in res["update_queue"]}
        self.assertEqual(new["S1"], 598.0)
        self.assertEqual(new["S2"], 429.0)
        self.assertEqual([a["sku"] for a in res["guard_asks"]], ["S2"])

    def test_drop_pause_event_recorded_even_without_price_change(self):
        # Уже стоим ровно на пределе: 15% под пиком 600 = 510.
        offers = [me(510), kaspi_offer("1", 450, rating=4.9, reviews=900)]
        moves = {"S1": [{"created_at": NOW - timedelta(hours=5), "our_old": 600.0, "our_new": 510.0,
                         "rival_price": 512.0, "action": "DUMP_TOP_1"}]}
        res = self.run_brain({"S1": snapshot("S1", 510, offers)},
                             guard_data={"store_defaults": {"enabled": True}, "moves_by_sku": moves})
        self.assertEqual(res["update_queue"], [])
        self.assertEqual(res["guard_events"][0]["action"], guard.ACTION_DAILY_DROP)
        self.assertEqual(res["guard_alerts"][0]["sku"], "S1")

    def test_observe_mode_keeps_price_reports_would(self):
        offers = [me(433), kaspi_offer("20437934", 431, rating=5.0, reviews=24), kaspi_offer("2", 600, rating=4.9, reviews=900)]
        res = self.run_brain({"S1": snapshot("S1", 433, offers)},
                             guard_data={"store_defaults": {"enabled": True, "observe_only": True}})
        self.assertEqual(res["update_queue"][0]["new_price"], 429.0)   # цена — как без защиты
        g = res["price_war_items"][0]["guard"]
        self.assertTrue(g["observe"])
        self.assertEqual(g["would"]["price"], 598.0)                   # а защита поставила бы 598
        self.assertIsNone(g["limit"])
        self.assertEqual(res["guard_events"], [])


class TestOwnerDecisions(unittest.TestCase):
    """Цифры и правила, согласованные с владельцем 03.10.2026."""

    def test_defaults(self):
        g = guard.GuardConfig()
        self.assertFalse(g.enabled)
        self.assertEqual(g.min_reviews, 50)
        self.assertEqual(g.no_rating_action, "ASK")
        self.assertIsNone(g.floor_below_next_pct)
        self.assertEqual(g.max_daily_drop_pct, 15.0)
        self.assertEqual(g.drop_pause_hours, 3)
        self.assertTrue(g.drop_alert)
        self.assertEqual(g.ladder_response, "RESET_MAX")
        self.assertEqual(g.all_ignored_action, "HOLD")

    def test_floor_ignores_preorder_reference(self):
        # T114-0023-1, 03.10: RA-BEST 3400 (355 отзывов), следующий — предзаказ на 18 дней за 6000.
        offers = [me(3398), kaspi_offer("1", 3400, name="RA-BEST", rating=4.9, reviews=355),
                  kaspi_offer("2", 6000, name="B&H", rating=4.8, reviews=300, days=18, preorder=1)]
        g = guard.GuardConfig(enabled=True, floor_below_next_pct=15)
        res = DumpingEngine.calculate_new_price(cfg(3398, min_price=2000.0, max_price=9000.0), ME, offers,
                                                guard_cfg=g, guard_ctx={"now": NOW})
        self.assertEqual(res["targetPrice"], 3398.0)  # остаёмся первыми, не 5100
        self.assertIsNone(res["guard"]["limit"])

    def test_drop_limit_never_raises_price(self):
        offers = [me(400), kaspi_offer("1", 350, rating=4.7, reviews=500)]
        g = guard.GuardConfig(enabled=True)
        res = DumpingEngine.calculate_new_price(cfg(400, min_price=100.0), ME, offers, guard_cfg=g,
                                                guard_ctx={"now": NOW, "peak_24h": 600.0})
        self.assertEqual(res["targetPrice"], 400.0)
        self.assertEqual(res["action"], guard.ACTION_DAILY_DROP)

    def test_drop_pause_then_continue_from_pause_level(self):
        g = guard.GuardConfig(enabled=True)
        pause_move = {"created_at": NOW - timedelta(hours=1), "our_old": 520.0, "our_new": 510.0,
                      "action": guard.ACTION_DAILY_DROP}
        history = [{"created_at": NOW - timedelta(hours=6), "our_old": 600.0, "our_new": 520.0, "action": "DUMP_TOP_1"},
                   pause_move]
        offers = [me(510), kaspi_offer("1", 450, rating=4.7, reviews=500)]
        c = cfg(510, min_price=100.0)
        # Через час после начала паузы — стоим.
        res = DumpingEngine.calculate_new_price(c, ME, offers, guard_cfg=g, guard_ctx={"now": NOW, "moves": history})
        self.assertEqual(res["targetPrice"], 510.0)
        self.assertEqual(res["action"], guard.ACTION_DROP_PAUSE)
        self.assertIsNotNone(res["guard"]["pause_until"])
        # Через 4 часа пауза кончилась: можно ещё на 15% от 510 → 434, а не от пика 600.
        later = NOW + timedelta(hours=3)
        res = DumpingEngine.calculate_new_price(c, ME, offers, guard_cfg=g, guard_ctx={"now": later, "moves": history})
        self.assertEqual(res["targetPrice"], 448.0)  # 450 − 2, выше предела 434 — ограничения нет
        self.assertIsNone(res["guard"]["limit"])

    def ladder_offers(self, our, rival_price):
        return [me(our), kaspi_offer("20437934", rival_price, rating=5.0, reviews=240),
                kaspi_offer("2", 900, rating=4.8, reviews=400)]

    def test_ladder_reset_to_start_when_first_or_second(self):
        g = guard.GuardConfig(enabled=True, **OLD_LADDER)
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=g,
                                                guard_ctx={"now": NOW, "moves": ladder_moves(620, 6)})
        self.assertEqual(res["targetPrice"], 620.0)  # к началу лесенки, а не на max_price 900
        self.assertEqual(res["action"], guard.ACTION_LADDER_RESET)
        self.assertEqual(res["guard"]["event"], guard.ACTION_LADDER_RESET)

    def test_no_reset_when_third_or_lower(self):
        g = guard.GuardConfig(enabled=True, **OLD_LADDER)
        offers = self.ladder_offers(587, 586) + [kaspi_offer("3", 500, rating=4.8, reviews=400)]
        res = DumpingEngine.calculate_new_price(cfg(587), ME, offers, guard_cfg=g,
                                                guard_ctx={"now": NOW, "moves": ladder_moves(620, 6)})
        self.assertNotEqual(res["action"], guard.ACTION_LADDER_RESET)

    def test_reset_once_a_day_and_clears_ladder(self):
        g = guard.GuardConfig(enabled=True, **OLD_LADDER)
        reset = {"created_at": NOW - timedelta(hours=3), "our_old": 560.0, "our_new": 890.0,
                 "action": guard.ACTION_LADDER_RESET}
        # Лесенка до сброса не считается.
        moves = ladder_moves(620, 6, t0=NOW - timedelta(hours=5)) + [reset]
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=g,
                                                guard_ctx={"now": NOW, "moves": moves})
        self.assertIsNone(res["guard"]["ladder"])
        # Новая стычка после охлаждения: при одном охлаждении в сутки второй раз не прыгаем...
        moves2 = moves + ladder_moves(880, 6, t0=NOW - timedelta(hours=2))
        g1 = guard.GuardConfig(enabled=True, cooling_per_day=1, **OLD_LADDER)
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=g1,
                                                guard_ctx={"now": NOW, "moves": moves2})
        self.assertIsNotNone(res["guard"]["ladder"])
        self.assertNotEqual(res["action"], guard.ACTION_LADDER_RESET)
        # ...а при двух (по умолчанию) и 3+ часах после первого — охлаждаем снова.
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=g,
                                                guard_ctx={"now": NOW, "moves": moves2})
        self.assertEqual(res["action"], guard.ACTION_LADDER_RESET)

    def test_reset_jump_not_counted_as_drop_peak(self):
        g = guard.GuardConfig(enabled=True)
        reset = {"created_at": NOW - timedelta(hours=1), "our_old": 560.0, "our_new": 890.0,
                 "action": guard.ACTION_LADDER_RESET}
        st = guard.drop_state([reset], g, current=890.0, now=NOW)
        self.assertEqual(st.peak, 560.0)  # от цены до прыжка, а не от самого прыжка
        st = guard.drop_state([reset], g, current=600.0, now=NOW)
        self.assertEqual(st.peak, 560.0)
        # Рынок сам поднялся выше прыжка и продержался — падение оттуда уже настоящее.
        reset = {**reset, "created_at": NOW - timedelta(hours=4)}
        up = {"created_at": NOW - timedelta(hours=3), "our_old": 890.0, "our_new": 1000.0,
              "action": "PULL_UP_PRICE"}
        st = guard.drop_state([reset, up], g, current=990.0, now=NOW)
        self.assertEqual(st.peak, 1000.0)

    def test_cooling_not_followed_returns_down_freely(self):
        # X172-0009-1, 05.10.2026: 147 → 192, соперник остался на 146.
        g = guard.GuardConfig(enabled=True)
        reset = {"created_at": NOW - timedelta(minutes=14), "our_old": 147.0, "our_new": 192.0,
                 "action": guard.ACTION_LADDER_RESET}
        offers = [me(192), kaspi_offer("1", 146, rating=4.9, reviews=300)]
        c = cfg(192, min_price=100.0)
        # Первые полчаса ждём соперника — цену не снижаем.
        res = DumpingEngine.calculate_new_price(c, ME, offers, guard_cfg=g,
                                                guard_ctx={"now": NOW, "moves": [reset]})
        self.assertEqual(res["targetPrice"], 192.0)
        self.assertEqual(res["action"], guard.HOLD_COOLING)
        self.assertIsNotNone(res["guard"]["cooling_until"])
        # Не поднялся — возвращаемся под него без паузы «резкое падение».
        later = NOW + timedelta(minutes=20)
        res = DumpingEngine.calculate_new_price(c, ME, offers, guard_cfg=g,
                                                guard_ctx={"now": later, "moves": [reset]})
        self.assertEqual(res["targetPrice"], 144.0)
        self.assertIsNone(res["guard"]["limit"])

    def test_cooling_cancels_earlier_pause(self):
        # T079-0005-3, 04.10.2026: пауза в 21:54, прыжок в 22:08 — пауза держала 715 три часа.
        g = guard.GuardConfig(enabled=True)
        moves = [{"created_at": NOW - timedelta(minutes=30), "our_old": 610.0, "our_new": 608.0,
                  "action": guard.ACTION_DAILY_DROP},
                 {"created_at": NOW - timedelta(minutes=15), "our_old": 608.0, "our_new": 715.0,
                  "action": guard.ACTION_LADDER_RESET}]
        st = guard.drop_state(moves, g, current=715.0, now=NOW)
        self.assertIsNone(st.pause_until)
        self.assertEqual(st.peak, 608.0)

    def test_validation_new_fields(self):
        with self.assertRaises(guard.GuardValidationError):
            guard.clean_guard_settings({"no_rating_action": "NUKE"})
        with self.assertRaises(guard.GuardValidationError):
            guard.clean_guard_settings({"drop_pause_hours": 0})
        self.assertEqual(guard.clean_guard_settings({"ladder_response": "RESET_MAX"})["ladder_response"], "RESET_MAX")


class TestDecisionStorage(unittest.TestCase):
    """Очередь вопросов менеджеру: новая пара «товар + продавец» создаётся
    один раз, решённая не возвращается в очередь, чужой магазин не трогает."""

    def setUp(self):
        import shutil, tempfile
        from unittest.mock import patch
        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from backend.db.models import Store
        from backend import state_helpers
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        self._tmp = tempfile.mkdtemp()
        self._rm = lambda: shutil.rmtree(self._tmp, ignore_errors=True)
        engine = create_engine(f"sqlite:///{os.path.join(self._tmp, 'g.db').replace(os.sep, '/')}")
        Base.metadata.create_all(engine)
        self.sh = state_helpers
        self._p = patch.object(state_helpers, "_get_sync_db_session", side_effect=lambda: Session(engine))
        self._p.start()
        s = state_helpers._get_sync_db_session()
        s.add(Store(id="st", merchant_id="m1", name="A"))
        s.add(Store(id="other", merchant_id="m2", name="B"))
        s.commit()
        s.close()

    def tearDown(self):
        self._p.stop()
        self._rm()

    def ask(self, sku="S1", mid="20460997", price=431.0):
        return {"sku": sku, "product_name": "Товар", "merchant_id": mid, "merchant_name": "Пустышка",
                "price": price, "our_price": 433.0}

    def test_created_once_then_updated(self):
        first = self.sh._upsert_guard_asks([self.ask()], store_id="st")
        self.assertEqual(len(first), 1)
        again = self.sh._upsert_guard_asks([self.ask(price=429.0)], store_id="st")
        self.assertEqual(again, [])  # второй раз менеджеру не шлём
        pending = self.sh._list_guard_decisions("st", pending_only=True)
        self.assertEqual(pending[0]["price"], 429.0)
        self.assertEqual(self.sh._load_guard_decisions("st"), {"S1": {"20460997": "ASK"}})

    def test_decision_leaves_queue_and_feeds_engine(self):
        row = self.sh._upsert_guard_asks([self.ask()], store_id="st")[0]
        self.assertIsNone(self.sh._set_guard_decision(row["id"], "IGNORE", "x", store_id="other"))
        self.sh._set_guard_decision(row["id"], "IGNORE", "owner@example.com", store_id="st")
        self.assertEqual(self.sh._list_guard_decisions("st", pending_only=True), [])
        self.assertEqual(self.sh._load_guard_decisions("st"), {"S1": {"20460997": "IGNORE"}})
        self.assertEqual(self.sh._upsert_guard_asks([self.ask()], store_id="st"), [])
        decided = self.sh._list_guard_decisions("st")
        self.assertEqual(decided[0]["decided_by"], "owner@example.com")


class TestAttacksReport(unittest.TestCase):
    def test_summary_counts_and_loss(self):
        snaps = [
            {"sku": "X974-0001-2", "name": "Подставка", "our_price": 433, "guard": {
                "signals": ["weak_rival", "ladder"], "ladder": {"rival_id": "20437934", "rival_name": "ИП КАРАБАЕВА"},
                "suspects": [{"merchant_id": "20437934", "name": "ИП КАРАБАЕВА", "adjacent": True, "reviews": 24}]}},
            {"sku": "S2", "name": "Другой", "our_price": 100, "guard": {"signals": []}},
        ]
        moves = {"X974-0001-2": ladder_moves(603, 3)}
        rep = dumping_attacks.summarize(snaps, moves)
        self.assertEqual(rep["summary"]["under_attack"], 1)
        self.assertEqual(rep["summary"]["ladders"], 1)
        self.assertEqual(rep["items"][0]["lost_per_unit"], 170.0)
        self.assertEqual(rep["rivals"][0]["merchant_id"], "20437934")
        self.assertEqual(rep["rivals"][0]["kinds"], ["ladder", "weak_rival"])

    def test_attack_state_filter(self):
        products = [{"attack_signals": ["ladder"], "guard_state": None},
                    {"attack_signals": [], "guard_state": {"limit": "GUARD_FLOOR_NEXT", "ignored_count": 0}},
                    {"attack_signals": [], "guard_state": None}]
        self.assertEqual(len(dumping_filters.apply_dumping_filters(products, attack_state="UNDER_ATTACK")), 1)
        self.assertEqual(len(dumping_filters.apply_dumping_filters(products, attack_state="GUARDED")), 1)
        facets = dumping_filters.facet_counts(products)
        self.assertEqual(facets["LADDER"], 1)


class TestBackfillParse(unittest.TestCase):
    def test_parses_executor_line(self):
        msg = ("🚀 [Executor] Авто-демпинг X974-0001-2 (город 750000000): 603 ₸ -> 600 ₸ "
               "(конкурент: 602 ₸, действие: DUMP_TOP_1, позиция: 2) (в очереди на отправку, приоритет 1)")
        self.assertEqual(parse_log_message(msg), {"sku": "X974-0001-2", "our_old": 603.0, "our_new": 600.0,
                                                  "rival_price": 602.0, "action": "DUMP_TOP_1"})


if __name__ == "__main__":
    unittest.main()


class TestObservationFixes(unittest.TestCase):
    """Правки по суткам наблюдения Бархана 04.10.2026 (HANDOFF §3.112)."""

    def test_war_in_place_is_not_ladder(self):
        g = guard.GuardConfig(enabled=True)
        # 20 ответов, но цена ушла 1000 → 940 (6%): обычная война, не лесенка.
        self.assertIsNone(guard.detect_ladder(ladder_moves(1000, 20), g, step=2.0, now=NOW))

    def test_steep_slide_is_ladder(self):
        g = guard.GuardConfig(enabled=True)
        sig = guard.detect_ladder(ladder_moves(300, 12), g, step=2.0, now=NOW)  # 300 → 264, −12%
        self.assertIsNotNone(sig)
        self.assertEqual(sig.price_from, 300)

    def test_too_few_answers_even_with_slide(self):
        g = guard.GuardConfig(enabled=True)
        self.assertIsNone(guard.detect_ladder(ladder_moves(100, 9), g, step=2.0, now=NOW))

    def test_reset_target_capped_by_max_price(self):
        sig = guard.detect_ladder(ladder_moves(620, 6), guard.GuardConfig(enabled=True, **OLD_LADDER), 2.0, NOW)
        self.assertEqual(guard.ladder_reset_target(sig, 587, 600), 600)
        self.assertEqual(guard.ladder_reset_target(sig, 587, 0), 620)
        self.assertIsNone(guard.ladder_reset_target(sig, 620, 900))  # уже там — прыгать некуда

    def test_radar_stub_equal_to_us_holds(self):
        stub = [{"merchantId": "COMPETITOR_TOP_1", "merchantName": "Конкурент (Top 1)", "price": 600.0}]
        for g in (None, guard.GuardConfig(enabled=True)):
            res = DumpingEngine.calculate_new_price(cfg(600), ME, stub, guard_cfg=g, guard_ctx={"now": NOW})
            self.assertEqual(res["targetPrice"], 600)
            self.assertEqual(res["action"], "HOLD_POSITION_NO_DATA")

    def test_radar_stub_below_us_is_followed_but_never_asked(self):
        stub = [{"merchantId": "COMPETITOR_TOP_1", "merchantName": "Конкурент (Top 1)", "price": 590.0}]
        res = DumpingEngine.calculate_new_price(cfg(600), ME, stub, guard_cfg=guard.GuardConfig(enabled=True),
                                                guard_ctx={"now": NOW})
        self.assertEqual(res["targetPrice"], 588)
        self.assertEqual(res["guard"]["asks"], [])
        self.assertFalse(guard.needs_decision({"merchant_id": "COMPETITOR_TOP_1", "rating": None},
                                               guard.GuardConfig()))

    def test_no_ask_when_decision_changes_nothing(self):
        # Без рейтинга за 300, нормальный продавец за 310, наш минимум 326:
        # с ним и без него стоим на минимуме — спрашивать незачем.
        offers = [me(326), kaspi_offer("20460997", 300), kaspi_offer("2", 310, rating=4.9, reviews=900)]
        res = DumpingEngine.calculate_new_price(cfg(326), ME, offers, guard_cfg=guard.GuardConfig(enabled=True),
                                                guard_ctx={"now": NOW})
        self.assertEqual(res["guard"]["asks"], [])

    def test_ask_below_min_price_when_it_holds_us_down(self):
        # А вот если за ним дальше только нормальный за 800 — без него встанем под 800.
        offers = [me(326), kaspi_offer("20460997", 300), kaspi_offer("2", 800, rating=4.9, reviews=900)]
        res = DumpingEngine.calculate_new_price(cfg(326), ME, offers, guard_cfg=guard.GuardConfig(enabled=True),
                                                guard_ctx={"now": NOW})
        [ask] = res["guard"]["asks"]
        self.assertEqual((ask["price_with"], ask["price_without"]), (326, 798))

    def test_ask_carries_price_with_and_without(self):
        offers = [me(433), kaspi_offer("20460997", 431), kaspi_offer("2", 800, rating=4.9, reviews=900)]
        res = DumpingEngine.calculate_new_price(cfg(433), ME, offers, guard_cfg=guard.GuardConfig(enabled=True),
                                                guard_ctx={"now": NOW})
        self.assertEqual(res["targetPrice"], 429)
        [ask] = res["guard"]["asks"]
        self.assertEqual((ask["price_with"], ask["price_without"]), (429, 798))
        self.assertEqual((ask["rival_rank"], ask["our_rank"], ask["offers_count"]), (1, 2, 3))

    def test_ask_message_is_informative(self):
        import backend.main  # noqa: F401 — как на проде; прямой импорт notifications ловит старый цикл
        from backend.dumper import guard_notify
        row = {"sku": "T219-0005-9", "product_name": "Термосумка", "url": "https://kaspi.kz/shop/p/t-123/",
               "merchant_id": "20473863", "merchant_name": "ИП ЯСМИНА", "price": 695, "our_price": 693,
               "rival_rank": 2, "our_rank": 1, "offers_count": 4, "delivery_days": 1, "min_price": 376,
               "price_with": 693, "price_without": 1250}
        text = guard_notify.ask_text(row, also_on=2)
        self.assertIn("(https://kaspi.kz/shop/p/t-123/)", text)
        self.assertIn("(https://kaspi.kz/shop/info/merchant/20473863/address-tab/)", text)
        self.assertIn("2-е место из 4", text)
        self.assertIn("ещё на 2 наших товарах", text)
        self.assertIn("1 250 ₸ (+557 ₸)", text)

    def test_no_ask_for_tiny_impact(self):
        # Без рейтинга за 436 — над нами на 3 ₸; без него встанем под 440, то есть +4 ₸: не спрашиваем.
        offers = [me(433), kaspi_offer("20460997", 436), kaspi_offer("2", 440, rating=4.9, reviews=900)]
        res = DumpingEngine.calculate_new_price(cfg(433), ME, offers, guard_cfg=guard.GuardConfig(enabled=True),
                                                guard_ctx={"now": NOW})
        self.assertEqual(res["guard"]["asks"], [])


# 05.10.2026 UTC 12:00 = 17:00 по Алматы (день), UTC 21:00 = 02:00 (ночь).
DAY = datetime(2026, 10, 5, 12, 0, 0)
NIGHT = datetime(2026, 10, 5, 21, 0, 0)


class TestCoolingAndNight(unittest.TestCase):
    def ladder_offers(self, our, rival_price):
        return [me(our), kaspi_offer("20437934", rival_price, rating=5.0, reviews=240),
                kaspi_offer("2", 900, rating=4.8, reviews=400)]

    def test_is_night_local_time(self):
        g = guard.GuardConfig()  # 1 → 8 по местному
        self.assertTrue(guard.is_night(g, NIGHT))
        self.assertFalse(guard.is_night(g, DAY))
        self.assertFalse(guard.is_night(g, datetime(2026, 10, 5, 3, 0)))   # 08:00 — уже день
        self.assertTrue(guard.is_night(g, datetime(2026, 10, 4, 20, 0)))   # 01:00
        wrap = guard.GuardConfig(night_from_hour=23, night_to_hour=7)
        self.assertTrue(guard.is_night(wrap, datetime(2026, 10, 5, 18, 30)))  # 23:30
        self.assertTrue(guard.is_night(wrap, datetime(2026, 10, 5, 1, 0)))    # 06:00
        self.assertFalse(guard.is_night(wrap, DAY))
        self.assertFalse(guard.is_night(guard.GuardConfig(night_from_hour=3, night_to_hour=3), NIGHT))

    def test_night_freeze_holds_but_allows_raise(self):
        g = guard.GuardConfig(enabled=True, night_freeze=True)
        c = cfg(600, min_price=100.0)
        cut = [me(600), kaspi_offer("1", 590, rating=4.9, reviews=300)]
        res = DumpingEngine.calculate_new_price(c, ME, cut, guard_cfg=g, guard_ctx={"now": NIGHT})
        self.assertEqual(res["targetPrice"], 600.0)
        self.assertEqual(res["action"], guard.HOLD_NIGHT)
        self.assertTrue(res["guard"]["night"])
        up = [me(600), kaspi_offer("1", 700, rating=4.9, reviews=300)]
        res = DumpingEngine.calculate_new_price(c, ME, up, guard_cfg=g, guard_ctx={"now": NIGHT})
        self.assertEqual(res["targetPrice"], 698.0)
        # Днём — обычный демпинг; без галочки — и ночью тоже.
        res = DumpingEngine.calculate_new_price(c, ME, cut, guard_cfg=g, guard_ctx={"now": DAY})
        self.assertEqual(res["targetPrice"], 588.0)
        res = DumpingEngine.calculate_new_price(c, ME, cut, guard_cfg=guard.GuardConfig(enabled=True),
                                                guard_ctx={"now": NIGHT})
        self.assertEqual(res["targetPrice"], 588.0)

    def test_no_cooling_at_night(self):
        g = guard.GuardConfig(enabled=True, **OLD_LADDER)
        moves = ladder_moves(620, 6, t0=NIGHT - timedelta(minutes=90))
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=g,
                                                guard_ctx={"now": NIGHT, "moves": moves})
        self.assertIsNotNone(res["guard"]["ladder"])
        self.assertNotEqual(res["action"], guard.ACTION_LADDER_RESET)

    def test_cooling_limits_per_day_and_gap(self):
        g = guard.GuardConfig(enabled=True, **OLD_LADDER)
        first = {"created_at": DAY - timedelta(hours=2), "our_old": 560.0, "our_new": 620.0,
                 "action": guard.ACTION_LADDER_RESET}
        moves = [first] + ladder_moves(620, 6, t0=DAY - timedelta(minutes=90))
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=g,
                                                guard_ctx={"now": DAY, "moves": moves})
        self.assertIsNotNone(res["guard"]["ladder"])
        self.assertNotEqual(res["action"], guard.ACTION_LADDER_RESET)  # меньше 3 часов после первого
        first2 = {**first, "created_at": DAY - timedelta(hours=4)}
        second = {**first, "created_at": DAY - timedelta(hours=8)}
        moves = [second, first2] + ladder_moves(620, 6, t0=DAY - timedelta(minutes=90))
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=g,
                                                guard_ctx={"now": DAY, "moves": moves})
        self.assertIsNotNone(res["guard"]["ladder"])
        self.assertNotEqual(res["action"], guard.ACTION_LADDER_RESET)  # два за сутки уже было
        moves = [first2] + ladder_moves(620, 6, t0=DAY - timedelta(minutes=90))
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=g,
                                                guard_ctx={"now": DAY, "moves": moves})
        self.assertEqual(res["action"], guard.ACTION_LADDER_RESET)  # второе за сутки, 4 ч спустя
        off = guard.GuardConfig(enabled=True, cooling_per_day=0, **OLD_LADDER)
        moves = ladder_moves(620, 6, t0=DAY - timedelta(minutes=90))
        res = DumpingEngine.calculate_new_price(cfg(587), ME, self.ladder_offers(587, 586), guard_cfg=off,
                                                guard_ctx={"now": DAY, "moves": moves})
        self.assertNotEqual(res["action"], guard.ACTION_LADDER_RESET)

    def test_new_settings_validated(self):
        with self.assertRaises(guard.GuardValidationError):
            guard.clean_guard_settings({"night_from_hour": 24})
        with self.assertRaises(guard.GuardValidationError):
            guard.clean_guard_settings({"cooling_hold_minutes": 500})
        out = guard.clean_guard_settings({"night_freeze": True, "cooling_per_day": 1})
        self.assertTrue(out["night_freeze"])
        self.assertEqual(out["cooling_per_day"], 1)
        # Старый сохранённый набор без новых полей получает умолчания.
        self.assertEqual(guard.GuardConfig.from_dict({"enabled": True}).cooling_hold_minutes, 30)


class TestPullUpPeak(unittest.TestCase):
    def test_night_pull_up_abandoned_by_rival_is_not_a_peak(self):
        # X172-0009 8шт, 06.10.2026: 04:18 по местному подтянулись 864 → 2000 за
        # соперником, к 08:02 он снова на 998 — спускаемся сразу, без пауз по 15%.
        g = guard.GuardConfig(enabled=True, night_freeze=True)
        moves = [{"created_at": datetime(2026, 10, 5, 19, 52), "our_old": 867.0, "our_new": 864.0,
                  "action": "DUMP_TOP_1"},
                 {"created_at": datetime(2026, 10, 5, 23, 18), "our_old": 864.0, "our_new": 2000.0,
                  "action": "PULL_UP_PRICE"}]
        morning = datetime(2026, 10, 6, 3, 2)
        offers = [me(2000), kaspi_offer("1", 998, rating=4.9, reviews=300)]
        res = DumpingEngine.calculate_new_price(cfg(2000, min_price=100.0, max_price=5000.0), ME, offers,
                                                guard_cfg=g, guard_ctx={"now": morning, "moves": moves})
        self.assertEqual(res["targetPrice"], 996.0)
        self.assertIsNone(res["guard"]["limit"])

    def test_short_day_pull_up_is_not_a_peak(self):
        # T219-0005-5: 669 → 804 за соперником, через 15 минут он на 668.
        g = guard.GuardConfig(enabled=True)
        moves = [{"created_at": DAY - timedelta(minutes=15), "our_old": 669.0, "our_new": 804.0,
                  "action": "PULL_UP_PRICE"}]
        st = guard.drop_state(moves, g, current=804.0, now=DAY)
        self.assertEqual(st.peak, 669.0)

    def test_held_pull_up_is_a_peak(self):
        g = guard.GuardConfig(enabled=True)
        moves = [{"created_at": DAY - timedelta(hours=3), "our_old": 669.0, "our_new": 804.0,
                  "action": "PULL_UP_PRICE"}]
        st = guard.drop_state(moves, g, current=804.0, now=DAY)
        self.assertEqual(st.peak, 804.0)
