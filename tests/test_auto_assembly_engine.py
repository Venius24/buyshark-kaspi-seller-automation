import os
import sys
import unittest
from datetime import datetime

sys.path.append(os.getcwd())

from backend.services.auto_assembly_engine import (
    AutoAssemblyRule,
    OrderSnapshot,
    evaluate_order,
    is_within_schedule,
)


def make_rule(**overrides) -> AutoAssemblyRule:
    defaults = dict(
        delivery_type="Express",
        enabled=True,
        dry_run=False,
        stage_accept=True,
        stage_assemble=True,
        stage_waybill=True,
        schedule=None,
        max_order_price=None,
        sku_blacklist=[],
        respect_stock_groups=True,
        max_per_cycle=None,
    )
    defaults.update(overrides)
    return AutoAssemblyRule(**defaults)


def make_order(**overrides) -> OrderSnapshot:
    defaults = dict(code="177419036", delivery_type="Express", price=5000.0, skus=["SKU-1"])
    defaults.update(overrides)
    return OrderSnapshot(**defaults)


# Понедельник, 12:00 — удобная фиксированная точка отсчёта для тестов расписания
MONDAY_NOON = datetime(2026, 8, 3, 12, 0)


class TestIsWithinSchedule(unittest.TestCase):
    def test_none_schedule_is_always_open(self):
        self.assertTrue(is_within_schedule(None, MONDAY_NOON))

    def test_empty_schedule_is_always_open(self):
        self.assertTrue(is_within_schedule({}, MONDAY_NOON))

    def test_within_time_window(self):
        schedule = {"start": "09:00", "end": "19:00"}
        self.assertTrue(is_within_schedule(schedule, MONDAY_NOON))

    def test_outside_time_window(self):
        schedule = {"start": "09:00", "end": "19:00"}
        late = MONDAY_NOON.replace(hour=20)
        self.assertFalse(is_within_schedule(schedule, late))

    def test_overnight_window_wraps_midnight(self):
        schedule = {"start": "22:00", "end": "06:00"}
        self.assertTrue(is_within_schedule(schedule, MONDAY_NOON.replace(hour=23)))
        self.assertTrue(is_within_schedule(schedule, MONDAY_NOON.replace(hour=2)))
        self.assertFalse(is_within_schedule(schedule, MONDAY_NOON.replace(hour=12)))

    def test_day_of_week_filter(self):
        schedule = {"days": [5, 6]}  # только Сб/Вс
        self.assertFalse(is_within_schedule(schedule, MONDAY_NOON))  # понедельник = 0
        saturday = MONDAY_NOON.replace(day=8)  # 2026-08-08 = суббота
        self.assertTrue(is_within_schedule(schedule, saturday))


class TestEvaluateOrder(unittest.TestCase):
    def test_no_rule_skips(self):
        result = evaluate_order(make_order(), None, MONDAY_NOON)
        self.assertEqual(result.decision, "SKIP_NO_RULE")
        self.assertEqual(result.allowed_stages, frozenset())

    def test_disabled_rule_skips(self):
        result = evaluate_order(make_order(), make_rule(enabled=False), MONDAY_NOON)
        self.assertEqual(result.decision, "SKIP_DISABLED")

    def test_already_excluded_skips_without_reevaluating_window(self):
        rule = make_rule(schedule={"start": "09:00", "end": "19:00"})
        result = evaluate_order(make_order(), rule, MONDAY_NOON, already_excluded=True)
        self.assertEqual(result.decision, "SKIP_ALREADY_EXCLUDED")
        self.assertFalse(result.mark_excluded_forever)

    def test_out_of_window_marks_excluded_forever(self):
        rule = make_rule(schedule={"start": "09:00", "end": "19:00"})
        late = MONDAY_NOON.replace(hour=21)
        result = evaluate_order(make_order(), rule, late)
        self.assertEqual(result.decision, "SKIP_OUT_OF_WINDOW")
        self.assertTrue(result.mark_excluded_forever)
        self.assertEqual(result.allowed_stages, frozenset())

    def test_price_limit_skips(self):
        rule = make_rule(max_order_price=3000.0)
        result = evaluate_order(make_order(price=5000.0), rule, MONDAY_NOON)
        self.assertEqual(result.decision, "SKIP_PRICE_LIMIT")

    def test_price_within_limit_proceeds(self):
        rule = make_rule(max_order_price=10000.0)
        result = evaluate_order(make_order(price=5000.0), rule, MONDAY_NOON)
        self.assertEqual(result.decision, "PROCEED")

    def test_sku_blacklist_skips(self):
        rule = make_rule(sku_blacklist=["sku-1"])
        result = evaluate_order(make_order(skus=["SKU-1", "SKU-2"]), rule, MONDAY_NOON)
        self.assertEqual(result.decision, "SKIP_SKU_BLACKLIST")

    def test_sku_not_in_blacklist_proceeds(self):
        rule = make_rule(sku_blacklist=["sku-9"])
        result = evaluate_order(make_order(skus=["SKU-1"]), rule, MONDAY_NOON)
        self.assertEqual(result.decision, "PROCEED")

    def test_no_stock_skips_when_respected(self):
        rule = make_rule(respect_stock_groups=True)
        result = evaluate_order(make_order(), rule, MONDAY_NOON, stock_ok=False)
        self.assertEqual(result.decision, "SKIP_NO_STOCK")

    def test_no_stock_ignored_when_not_respected(self):
        rule = make_rule(respect_stock_groups=False)
        result = evaluate_order(make_order(), rule, MONDAY_NOON, stock_ok=False)
        self.assertEqual(result.decision, "PROCEED")

    def test_cycle_limit_skips(self):
        rule = make_rule(max_per_cycle=2)
        result = evaluate_order(make_order(), rule, MONDAY_NOON, orders_processed_this_cycle=2)
        self.assertEqual(result.decision, "SKIP_CYCLE_LIMIT")

    def test_cycle_limit_not_yet_reached_proceeds(self):
        rule = make_rule(max_per_cycle=2)
        result = evaluate_order(make_order(), rule, MONDAY_NOON, orders_processed_this_cycle=1)
        self.assertEqual(result.decision, "PROCEED")

    def test_proceed_returns_only_enabled_stages(self):
        rule = make_rule(stage_accept=True, stage_assemble=False, stage_waybill=True)
        result = evaluate_order(make_order(), rule, MONDAY_NOON)
        self.assertEqual(result.decision, "PROCEED")
        self.assertEqual(result.allowed_stages, frozenset({"accept", "waybill"}))

    def test_proceed_carries_dry_run_flag(self):
        rule = make_rule(dry_run=True)
        result = evaluate_order(make_order(), rule, MONDAY_NOON)
        self.assertTrue(result.dry_run)

    def test_all_stages_disabled_still_proceeds_with_empty_stages(self):
        rule = make_rule(stage_accept=False, stage_assemble=False, stage_waybill=False)
        result = evaluate_order(make_order(), rule, MONDAY_NOON)
        self.assertEqual(result.decision, "PROCEED")
        self.assertEqual(result.allowed_stages, frozenset())


if __name__ == "__main__":
    unittest.main()
