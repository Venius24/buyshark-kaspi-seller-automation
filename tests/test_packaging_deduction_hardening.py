"""
Списание остатка по вкладкам упаковки — ужесточение (2026-08-13, HANDOFF 3.46).

Три претензии владельца: мастер-группа должна ТОЧНО видеть списание своего
артикула, без галлюнов (не зацепить чужой товар), без дублей (не вычесть
дважды) и без потерь (не проспать заказ, проскочивший мимо «На сборке»).
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from backend.services import stock_sync_worker as worker
from backend.services.stock_sync_worker import (
    _calculate_physical_units,
    _normalize_sku,
    match_group_for_sku,
    process_packaging_auto_deduction,
)

TOKEN = "fake-token"
STORE = "store-test"


def group(gid, name, skus, stock=10):
    return {"group_id": gid, "group_name": name, "shared_stock": stock,
            "linked_skus": skus, "is_active": True, "sync_logs": []}


class TestNormalization(unittest.TestCase):
    def test_strips_handwritten_tail(self):
        """В merchantSku попадает человеческая приписка через пробел."""
        self.assertEqual(_normalize_sku("H422-0007-9 2шт"), "h422-0007-9")
        self.assertEqual(_normalize_sku("X474-0018-6 1шт"), "x474-0018-6")

    def test_case_and_spaces(self):
        self.assertEqual(_normalize_sku("  T219-0005  "), "t219-0005")

    def test_empty(self):
        self.assertEqual(_normalize_sku(None), "")

    def test_units_use_normalized_sku(self):
        """Мультипак считается и по грязному артикулу."""
        self.assertEqual(_calculate_physical_units("T219-0005-4 комплект", 1), 4)


class TestMatching(unittest.TestCase):
    def test_exact_match(self):
        groups = {"a": group("a", "A", ["T219-0005"])}
        gid, g = match_group_for_sku("T219-0005", groups)
        self.assertEqual(gid, "a")

    def test_multipack_matches_its_base(self):
        groups = {"a": group("a", "A", ["T219-0005"])}
        gid, _ = match_group_for_sku("T219-0005-4", groups)
        self.assertEqual(gid, "a")

    def test_dirty_sku_still_matches(self):
        """Единственный случай, который ловило старое широкое правило."""
        groups = {"a": group("a", "A", ["H422-0007-9"])}
        gid, _ = match_group_for_sku("H422-0007-9 2шт", groups)
        self.assertEqual(gid, "a")

    def test_title_is_not_used_anymore(self):
        """Раньше ключ группы искали в НАЗВАНИИ товара — источник галлюнов."""
        groups = {"a": group("a", "A", ["T219-0005"])}
        gid, _ = match_group_for_sku("D697-0011", groups)
        self.assertIsNone(gid)

    def test_foreign_sku_with_common_prefix_is_not_matched(self):
        """K471-0005 не должен списываться у группы K471-0001."""
        groups = {"a": group("a", "K471-0001", ["K471-0001", "K471-0001-2"])}
        gid, _ = match_group_for_sku("K471-0005-1", groups)
        self.assertIsNone(gid)

    def test_exact_beats_shared_multipack_base(self):
        """K474-0003-1 и K474-0003-2 в РАЗНЫХ группах не должны схлопнуться."""
        groups = {
            "a": group("a", "A", ["K474-0003-1"]),
            "b": group("b", "B", ["K474-0003-2"]),
        }
        self.assertEqual(match_group_for_sku("K474-0003-1", groups)[0], "a")
        self.assertEqual(match_group_for_sku("K474-0003-2", groups)[0], "b")

    def test_ambiguous_match_deducts_nothing(self):
        """Две группы претендуют на артикул — не угадываем."""
        groups = {
            "a": group("a", "A", ["T219-0005"]),
            "b": group("b", "B", ["T219-0005"]),
        }
        gid, g = match_group_for_sku("T219-0005", groups)
        self.assertIsNone(gid)
        self.assertIsNone(g)


class DeductionTestCase(unittest.TestCase):
    """Общая обвязка: список групп в памяти, никаких походов в Kaspi/БД."""

    def setUp(self):
        self.groups = {}
        self.saved = []
        self.claims = {}
        self.processed = {}
        self.broadcasts = []

        p = [
            patch.object(worker, "_load_master_stock_groups", side_effect=lambda store_id=None: self.groups),
            patch.object(worker, "_save_master_stock_groups", side_effect=lambda g, store_id=None: self.saved.append(g)),
            patch("backend.state_helpers._load_processed_packaging_orders", side_effect=lambda sid: self.processed),
            patch("backend.state_helpers._save_processed_packaging_orders", side_effect=lambda d, sid: None),
            patch("backend.state_helpers._load_processed_orders_by_kind", side_effect=lambda sid, kind: dict(self.claims)),
            patch("backend.state_helpers._save_processed_orders_by_kind", side_effect=lambda d, sid, kind: self.claims.update(d)),
            patch.object(worker, "execute_broadcast_sync", side_effect=self._record_broadcast),
        ]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def _record_broadcast(self, gid, token, store_id=None, trigger_order_info=None):
        self.broadcasts.append((gid, trigger_order_info))
        async def noop():
            return None
        return noop()

    def run_cycle(self, tabs):
        with patch("backend.kaspi_wrapper.KaspiAPI.get_all_packaging_tabs", return_value=tabs):
            return asyncio.run(process_packaging_auto_deduction(TOKEN, STORE))


class TestSafetyNetTab(DeductionTestCase):
    def test_order_seen_only_in_transfer_tab_is_deducted(self):
        """Заказ упаковали между тиками — «На сборке» его уже не покажет."""
        self.groups = {"a": group("a", "A", ["T219-0005"], stock=10)}
        res = self.run_cycle({
            "cargo_assembly": [],
            "wait_for_courier": [
                {"code": "555", "items": [{"merchant_sku": "T219-0005", "title": "t", "quantity": 1}]}
            ],
        })
        self.assertEqual(res["deducted_orders_count"], 1)
        self.assertEqual(self.groups["a"]["shared_stock"], 9)

    def test_same_order_in_both_tabs_deducts_once(self):
        """Заказ может попасть в обе выборки — вычитаем один раз."""
        self.groups = {"a": group("a", "A", ["T219-0005"], stock=10)}
        order = {"code": "555", "items": [{"merchant_sku": "T219-0005", "title": "t", "quantity": 1}]}
        res = self.run_cycle({"cargo_assembly": [order], "wait_for_courier": [order]})
        self.assertEqual(res["deducted_orders_count"], 1)
        self.assertEqual(self.groups["a"]["shared_stock"], 9)


class TestMultiPosition(DeductionTestCase):
    def test_two_positions_deduct_two_groups(self):
        """Раньше списывалась только первая позиция — вторая группа о продаже не узнавала."""
        self.groups = {
            "a": group("a", "A", ["S517-0085"], stock=10),
            "b": group("b", "B", ["S517-0054"], stock=20),
        }
        res = self.run_cycle({"cargo_assembly": [{
            "code": "777",
            "items": [
                {"merchant_sku": "S517-0085", "title": "x", "quantity": 1},
                {"merchant_sku": "S517-0054-2", "title": "y", "quantity": 1},
            ],
        }], "wait_for_courier": []})
        self.assertEqual(res["deducted_orders_count"], 2)
        self.assertEqual(self.groups["a"]["shared_stock"], 9)
        self.assertEqual(self.groups["b"]["shared_stock"], 18)

    def test_two_positions_of_one_group_broadcast_once(self):
        """Две позиции одной группы — одна веерная рассылка, а не две."""
        self.groups = {"a": group("a", "A", ["S517-0085", "S517-0086"], stock=10)}
        self.run_cycle({"cargo_assembly": [{
            "code": "778",
            "items": [
                {"merchant_sku": "S517-0085", "title": "x", "quantity": 1},
                {"merchant_sku": "S517-0086", "title": "y", "quantity": 1},
            ],
        }], "wait_for_courier": []})
        self.assertEqual(len(self.broadcasts), 1)
        self.assertEqual(self.groups["a"]["shared_stock"], 8)


class TestNoDoubleDeduction(DeductionTestCase):
    def test_second_pass_does_not_deduct_again(self):
        self.groups = {"a": group("a", "A", ["T219-0005"], stock=10)}
        tabs = {"cargo_assembly": [
            {"code": "999", "items": [{"merchant_sku": "T219-0005", "title": "t", "quantity": 1}]}
        ], "wait_for_courier": []}
        self.run_cycle(tabs)
        res2 = self.run_cycle(tabs)
        self.assertEqual(res2["deducted_orders_count"], 0)
        self.assertEqual(self.groups["a"]["shared_stock"], 9)

    def test_legacy_order_level_claim_blocks_new_key(self):
        """Исторические заказы забронированы ключом без артикула — не пересчитываем."""
        self.groups = {"a": group("a", "A", ["T219-0005"], stock=10)}
        self.claims = {"1033348179": {"claimed_by": "packaging"}}
        res = self.run_cycle({"cargo_assembly": [
            {"code": "1033348179", "items": [{"merchant_sku": "T219-0005", "title": "t", "quantity": 1}]}
        ], "wait_for_courier": []})
        self.assertEqual(res["deducted_orders_count"], 0)
        self.assertEqual(self.groups["a"]["shared_stock"], 10)

    def test_ambiguous_sku_deducts_nothing(self):
        self.groups = {
            "a": group("a", "A", ["T219-0005"], stock=10),
            "b": group("b", "B", ["T219-0005"], stock=10),
        }
        res = self.run_cycle({"cargo_assembly": [
            {"code": "111", "items": [{"merchant_sku": "T219-0005", "title": "t", "quantity": 1}]}
        ], "wait_for_courier": []})
        self.assertEqual(res["deducted_orders_count"], 0)
        self.assertEqual(self.groups["a"]["shared_stock"], 10)
        self.assertEqual(self.groups["b"]["shared_stock"], 10)


class TestVisibility(DeductionTestCase):
    def test_group_status_and_journal_show_the_order(self):
        self.groups = {"a": group("a", "A", ["T219-0005"], stock=10)}
        self.run_cycle({"cargo_assembly": [
            {"code": "424242", "items": [{"merchant_sku": "T219-0005", "title": "Термуха", "quantity": 1}]}
        ], "wait_for_courier": []})

        g = self.groups["a"]
        self.assertEqual(g["status"], "deducting")
        self.assertIn("424242", g["last_sync_log"])
        self.assertIn("424242", g["sync_logs"][0])
        self.assertIn("T219-0005", g["sync_logs"][0])

    def test_broadcast_gets_the_trigger_order(self):
        self.groups = {"a": group("a", "A", ["T219-0005"], stock=10)}
        self.run_cycle({"cargo_assembly": [
            {"code": "424243", "items": [{"merchant_sku": "T219-0005", "title": "Термуха", "quantity": 2}]}
        ], "wait_for_courier": []})
        gid, trigger = self.broadcasts[0]
        self.assertEqual(gid, "a")
        self.assertEqual(trigger["order_code"], "424243")
        self.assertEqual(trigger["units"], 2)


if __name__ == "__main__":
    unittest.main()
