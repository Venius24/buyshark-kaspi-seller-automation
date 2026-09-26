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
    match_group_for_sku_indexed,
    build_group_index,
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

    def test_units_are_exactly_the_ordered_quantity(self):
        """Хвост артикула — номер варианта, а не размер упаковки."""
        self.assertEqual(_calculate_physical_units("T219-0005-4 комплект", 1), 1)
        self.assertEqual(_calculate_physical_units("T219-0005-4", 2), 2)

    def test_pack_size_comes_from_the_group_setting(self):
        """Размер комплекта задаётся руками, а не угадывается по артикулу."""
        self.assertEqual(_calculate_physical_units("T219-0005-4", 1, 7), 7)
        self.assertEqual(_calculate_physical_units("T219-0005-4", 3, 7), 21)

    def test_broken_pack_size_falls_back_to_one(self):
        """Ноль/None/мусор не должны обнулять списание."""
        self.assertEqual(_calculate_physical_units("T219-0005", 2, 0), 2)
        self.assertEqual(_calculate_physical_units("T219-0005", 2, None), 2)


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


class TestVerbatimSkuBeatsNormalized(unittest.TestCase):
    """Регрессия 14.08: приписка через пробел бывает значащей.

    У Бархана "H422-0007-9" (группа «H422-0007 1шт») и "H422-0007-9 2шт"
    (группа «H422-0007 2 шт») — разные товары. Нормализация схлопывала их в
    один ключ, обе группы отвечали как на свой, списание пропускалось на
    каждом проходе: 652 предупреждения за ночь и ни одного вычета.
    """

    def _groups(self):
        return {
            "one": group("one", "H422-0007 1шт", ["H422-0007-9", "H422-0007-8"], stock=59),
            "two": group("two", "H422-0007 2 шт", ["H422-0007-9 2шт", "H422-0007-6 2 шт"], stock=4),
        }

    def test_plain_sku_goes_to_single_pack_group(self):
        self.assertEqual(match_group_for_sku("H422-0007-9", self._groups())[0], "one")

    def test_annotated_sku_goes_to_double_pack_group(self):
        self.assertEqual(match_group_for_sku("H422-0007-9 2шт", self._groups())[0], "two")

    def test_spacing_and_case_do_not_matter(self):
        """«2 шт» с пробелом и другим регистром — тот же артикул."""
        self.assertEqual(match_group_for_sku("h422-0007-6   2  шт", self._groups())[0], "two")

    def test_normalized_fallback_survives(self):
        """Если группы с точной припиской нет — по-прежнему чистим хвост."""
        groups = {"a": group("a", "A", ["H422-0007-9"])}
        self.assertEqual(match_group_for_sku("H422-0007-9 2шт", groups)[0], "a")

    def test_two_groups_on_the_same_verbatim_sku_still_deduct_nothing(self):
        groups = {
            "a": group("a", "A", ["H422-0007-9 2шт"]),
            "b": group("b", "B", ["H422-0007-9 2шт"]),
        }
        self.assertIsNone(match_group_for_sku("H422-0007-9 2шт", groups)[0])


class TestClaimKeySeparatesAnnotatedSku(unittest.TestCase):
    """Две позиции одного заказа не должны считаться одной."""

    def test_annotated_and_plain_sku_get_different_keys(self):
        a = worker._claim_key("123", "H422-0007-9")
        b = worker._claim_key("123", "H422-0007-9 2шт")
        self.assertNotEqual(a, b)

    def test_legacy_key_is_still_recognized(self):
        """Старые брони писались по артикулу без приписки — не списываем заново."""
        claims = {worker._legacy_claim_key("123", "H422-0007-9 2шт"): {"claimed_by": "old"}}
        with patch("backend.state_helpers._load_processed_orders_by_kind", side_effect=lambda sid, kind, keys=None: dict(claims)), \
             patch("backend.state_helpers._save_processed_orders_by_kind", side_effect=lambda d, sid, kind: None):
            granted = worker.claim_stock_deduction(STORE, "123", "test", {}, sku="H422-0007-9 2шт")
        self.assertFalse(granted)


class TestPackSizeSetting(unittest.TestCase):
    """Рецепт артикула (`sku_recipes`) — единственный источник размера комплекта."""

    def test_default_group_deducts_one_for_one(self):
        from backend.services.sku_recipes import components_for
        groups = {"a": group("a", "A", ["T219-0005"])}
        self.assertEqual(components_for("a", groups, "T219-0005"), [("a", 1)])

    def test_sku_with_pack_deducts_the_pack(self):
        from backend.services.sku_recipes import components_for
        g = {**group("a", "Палочки", ["H422-0002-4"]), "sku_recipes": {"H422-0002-4": {"qty": 7}}}
        (_, per_unit), = components_for("a", {"a": g}, "H422-0002-4")
        self.assertEqual(_calculate_physical_units("H422-0002-4", 2, per_unit), 14)

    def test_legacy_group_field_is_ignored(self):
        """deduct_per_unit перенесён миграцией 044 в рецепты и больше не читается."""
        from backend.services.sku_recipes import components_for
        g = {**group("a", "A", ["T219-0005"]), "deduct_per_unit": 7}
        self.assertEqual(components_for("a", {"a": g}, "T219-0005"), [("a", 1)])


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
            patch("backend.state_helpers._load_processed_packaging_orders", side_effect=lambda sid, keys=None: dict(self.processed)),
            patch("backend.state_helpers._save_processed_packaging_orders", side_effect=lambda d, sid: self.processed.update(d)),
            patch("backend.state_helpers._load_processed_orders_by_kind", side_effect=lambda sid, kind, keys=None: dict(self.claims)),
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


class TestPackSizeInDeductionCycle(DeductionTestCase):
    """Комплект группы применяется в полном цикле списания."""

    def _sell(self, qty, per_unit, stock=100):
        g = group("a", "Палочки NG", ["H422-0002-4"], stock=stock)
        g["sku_recipes"] = {"H422-0002-4": {"qty": per_unit}}
        self.groups = {"a": g}
        return self.run_cycle({"cargo_assembly": [
            {"code": "777", "items": [{"merchant_sku": "H422-0002-4", "title": "t", "quantity": qty}]}
        ], "wait_for_courier": []})

    def test_pack_multiplies_the_deduction(self):
        res = self._sell(2, 7)
        self.assertEqual(res["deductions"][0]["units"], 14)
        self.assertEqual(self.groups["a"]["shared_stock"], 86)

    def test_journal_explains_where_the_number_came_from(self):
        self._sell(1, 7)
        self.assertIn("комплект 7", self.groups["a"]["last_sync_log"])

    def test_group_without_the_field_deducts_one_for_one(self):
        self.groups = {"a": group("a", "A", ["T219-0005"], stock=10)}
        res = self.run_cycle({"cargo_assembly": [
            {"code": "778", "items": [{"merchant_sku": "T219-0005", "title": "t", "quantity": 3}]}
        ], "wait_for_courier": []})
        self.assertEqual(res["deductions"][0]["units"], 3)
        self.assertNotIn("комплект", self.groups["a"]["last_sync_log"])


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
        self.assertEqual(self.groups["b"]["shared_stock"], 19)

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


class TestOversold(DeductionTestCase):
    """Заказов пришло больше, чем числилось остатка.

    Остаток обрезается по нулю — отрицательное число нельзя слать в Kaspi, —
    и до 14.08 нехватка на этом терялась: журнал писал «было 4 → стало 0» и
    ни слова о том, что трёх штук нет. На проде так ушла в минус группа
    «H422-0007 2 шт»: заказ на 7 шт. при остатке 4.
    """

    def setUp(self):
        super().setUp()
        self.alerts = []
        p = patch.object(worker, "notify_oversold_telegram", side_effect=self._record_alert)
        p.start()
        self.addCleanup(p.stop)

    def _record_alert(self, **kwargs):
        self.alerts.append(kwargs)
        async def noop():
            return None
        return noop()

    def _sell(self, qty, stock=4, code="1034264977"):
        self.groups = {"a": group("a", "H422-0007 2 шт", ["H422-0007 2шт"], stock=stock)}
        return self.run_cycle({"cargo_assembly": [
            {"code": code, "items": [{"merchant_sku": "H422-0007 2шт", "title": "t", "quantity": qty}]}
        ], "wait_for_courier": []})

    def test_stock_is_clamped_at_zero(self):
        self._sell(7)
        self.assertEqual(self.groups["a"]["shared_stock"], 0)

    def test_shortfall_is_recorded_as_debt(self):
        self._sell(7)
        self.assertEqual(self.groups["a"]["oversold"], 3)

    def test_shortfall_is_written_to_the_journal(self):
        self._sell(7)
        self.assertIn("не хватило 3 шт.", self.groups["a"]["last_sync_log"])

    def test_shortfall_is_reported_in_the_result(self):
        res = self._sell(7)
        self.assertEqual(res["deductions"][0]["shortfall"], 3)

    def test_debt_accumulates_across_orders(self):
        self.groups = {"a": group("a", "A", ["H422-0007 2шт"], stock=1)}
        self.run_cycle({"cargo_assembly": [
            {"code": "1", "items": [{"merchant_sku": "H422-0007 2шт", "title": "t", "quantity": 3}]},
            {"code": "2", "items": [{"merchant_sku": "H422-0007 2шт", "title": "t", "quantity": 4}]},
        ], "wait_for_courier": []})
        self.assertEqual(self.groups["a"]["shared_stock"], 0)
        self.assertEqual(self.groups["a"]["oversold"], 6)  # (3-1) + 4

    def test_urgent_alert_is_sent_once_per_order(self):
        self._sell(7)
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0]["debt"], 3)
        self.assertEqual(self.alerts[0]["old_stock"], 4)

    def test_normal_deduction_neither_alerts_nor_owes(self):
        self._sell(2)
        self.assertEqual(self.groups["a"]["shared_stock"], 2)
        self.assertEqual(self.groups["a"]["oversold"], 0)
        self.assertEqual(self.alerts, [])
        self.assertNotIn("не хватило", self.groups["a"]["last_sync_log"])

    def test_exactly_enough_stock_is_not_a_debt(self):
        self._sell(4)
        self.assertEqual(self.groups["a"]["shared_stock"], 0)
        self.assertEqual(self.groups["a"]["oversold"], 0)
        self.assertEqual(self.alerts, [])


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


class TestStatusNeverSticks(unittest.TestCase):
    """Индикатор «идёт списание» обязан гаснуть — даже когда рассылка упала.

    Рассылку всегда запускают в фоне, поэтому её исключение никто не читает, а
    статус ставится ДО похода в Kaspi. Без этих двух предохранителей карточка
    группы осталась бы со спиннером навсегда.
    """

    def setUp(self):
        self.groups = {"a": group("a", "A", ["T219-0005"])}
        p = [
            patch.object(worker, "_load_master_stock_groups", side_effect=lambda store_id=None: self.groups),
            patch.object(worker, "_save_master_stock_groups", side_effect=lambda g, store_id=None: None),
            patch.object(worker, "broadcast_ws_event", side_effect=self._noop_ws),
        ]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    async def _noop_ws(self, *a, **kw):
        return None

    def test_failed_broadcast_releases_the_badge(self):
        self.groups["a"]["status"] = "deducting"
        with patch.object(worker, "_execute_broadcast_sync_impl", side_effect=RuntimeError("Kaspi 500")):
            asyncio.run(worker.execute_broadcast_sync("a", "tok", store_id=STORE,
                                                      trigger_order_info={"order_code": "1"}))
        self.assertEqual(self.groups["a"]["status"], "warning")
        self.assertIn("не завершилась", self.groups["a"]["last_sync_log"])

    def test_failed_broadcast_does_not_raise_into_the_task(self):
        """Иначе упадёт весь тик упаковки, а не одна группа."""
        with patch.object(worker, "_execute_broadcast_sync_impl", side_effect=RuntimeError("boom")):
            asyncio.run(worker.execute_broadcast_sync("a", "tok", store_id=STORE))

    def test_successful_broadcast_is_not_touched(self):
        self.groups["a"]["status"] = "synced"
        with patch.object(worker, "_execute_broadcast_sync_impl", side_effect=self._noop_ws):
            asyncio.run(worker.execute_broadcast_sync("a", "tok", store_id=STORE))
        self.assertEqual(self.groups["a"]["status"], "synced")


class TestStuckStatusSweep(unittest.TestCase):
    """От kill -9 обёртка не спасает — статус снимает сторож на тике упаковки."""

    def _aged(self, seconds):
        import datetime as dt
        return (dt.datetime.now() - dt.timedelta(seconds=seconds)).isoformat()

    def test_old_deducting_is_released(self):
        g = group("a", "A", ["X"])
        g["status"], g["status_since"] = "deducting", self._aged(3600)
        self.assertTrue(worker._sweep_stuck_statuses({"a": g}))
        self.assertEqual(g["status"], "warning")
        self.assertNotIn("status_since", g)
        self.assertIn("оборвалась", g["sync_logs"][0])

    def test_fresh_deducting_is_left_alone(self):
        """Идущая прямо сейчас рассылка не должна сбрасываться."""
        g = group("a", "A", ["X"])
        g["status"], g["status_since"] = "deducting", self._aged(5)
        self.assertFalse(worker._sweep_stuck_statuses({"a": g}))
        self.assertEqual(g["status"], "deducting")

    def test_group_without_timestamp_gets_one(self):
        """Зависшие ещё до появления сторожа: сначала метка, снятие — следующим тиком."""
        g = group("a", "A", ["X"])
        g["status"] = "deducting"
        self.assertTrue(worker._sweep_stuck_statuses({"a": g}))
        self.assertEqual(g["status"], "deducting")
        self.assertIn("status_since", g)

    def test_synced_group_is_never_touched(self):
        g = group("a", "A", ["X"])
        g["status"] = "synced"
        self.assertFalse(worker._sweep_stuck_statuses({"a": g}))
        self.assertEqual(g["status"], "synced")

    def test_broken_timestamp_does_not_crash(self):
        g = group("a", "A", ["X"])
        g["status"], g["status_since"] = "updating", "не дата"
        self.assertTrue(worker._sweep_stuck_statuses({"a": g}))
        self.assertEqual(g["status"], "updating")


if __name__ == "__main__":
    unittest.main()


class TestIndexedMatcherEquivalence(unittest.TestCase):
    """build_group_index + match_group_for_sku_indexed — быстрый путь для
    матчинга по всему каталогу (прогноз остатков). Он обязан отвечать ровно
    то же, что перебор: расхождение означало бы списание/прогноз не у той
    группы. match_group_for_sku сама делегирует индексу, поэтому тест ловит
    именно расхождение правил, а не двух реализаций.
    """

    GROUPS = {
        "a": {"group_name": "A", "linked_skus": ["T219-0005", "H422-0007-9"]},
        "b": {"group_name": "B", "linked_skus": ["H422-0007-9 2шт", "K474-0003-1"]},
        "c": {"group_name": "C", "linked_skus": ["K474-0003-2", "D697-0011"]},
        "d": {"group_name": "D", "linked_skus": ["X470-0010"]},
        "e": {"group_name": "E", "linked_skus": ["X470-0010"]},  # намеренный дубль
    }

    CASES = [
        "T219-0005", "T219-0005-4", "H422-0007-9", "H422-0007-9 2шт",
        "h422-0007-9   2  шт", "K474-0003-1", "K474-0003-2", "K474-0003",
        "D697-0011", "D697-0011-3", "X470-0010", "X470-0010-2",
        "", "   ", "НЕТ-ТАКОГО", "t219-0005",
    ]

    def test_indexed_matches_direct_call(self):
        index = build_group_index(self.GROUPS)
        for sku in self.CASES:
            with self.subTest(sku=sku):
                self.assertEqual(
                    match_group_for_sku_indexed(sku, index)[0],
                    match_group_for_sku(sku, self.GROUPS)[0],
                )

    def test_index_is_reusable_across_lookups(self):
        """Индекс строится один раз и не портится последующими лукапами."""
        index = build_group_index(self.GROUPS)
        first = [match_group_for_sku_indexed(s, index)[0] for s in self.CASES]
        second = [match_group_for_sku_indexed(s, index)[0] for s in self.CASES]
        self.assertEqual(first, second)

    def test_ambiguous_sku_still_refuses(self):
        """Дубль привязки — по-прежнему отказ, а не угадывание."""
        index = build_group_index(self.GROUPS)
        self.assertIsNone(match_group_for_sku_indexed("X470-0010", index)[0])


class TestTickWritesOnlyDeltas(DeductionTestCase):
    """Тик сохраняет только изменённое — и группы, и отметки.

    Раньше он передавал в сохранение ВЕСЬ словарь: все 305 групп Бархана и все
    10 343 отметки, каждую отдельным SELECT+UPDATE, и всё это внутри
    store-лока. Пока сохранение шло (минутами), кабинет не мог ни поменять
    остаток, ни сохранить группу — PUT p95 = 48с, PATCH остатка = 24с.
    """

    def _three_groups(self):
        return {
            "a": group("a", "A", ["S517-0085"], stock=10),
            "b": group("b", "B", ["S517-0054"], stock=20),
            "c": group("c", "C", ["S517-0099"], stock=30),
        }

    def _sell_one(self):
        self.groups = self._three_groups()
        return self.run_cycle({"cargo_assembly": [{
            "code": "900",
            "items": [{"merchant_sku": "S517-0085", "title": "x", "quantity": 1}],
        }], "wait_for_courier": []})

    def test_only_the_touched_group_is_saved(self):
        self._sell_one()
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(set(self.saved[0]), {"a"})

    def test_untouched_groups_never_reach_the_writer(self):
        self._sell_one()
        written = set().union(*[set(batch) for batch in self.saved])
        self.assertNotIn("b", written)
        self.assertNotIn("c", written)

    def test_nothing_is_saved_when_nothing_changed(self):
        """Заказ уже отмечен — тик не должен трогать БД вообще."""
        self.groups = self._three_groups()
        order = {"code": "901", "items": [{"merchant_sku": "S517-0085", "title": "x", "quantity": 1}]}
        self.run_cycle({"cargo_assembly": [order], "wait_for_courier": []})
        self.saved.clear()
        self.run_cycle({"cargo_assembly": [order], "wait_for_courier": []})
        self.assertEqual(self.saved, [])

    def test_marks_are_written_as_a_delta_not_the_whole_dict(self):
        """Второй заказ дописывает свою отметку, а не переписывает обе."""
        self.groups = self._three_groups()
        self.run_cycle({"cargo_assembly": [{
            "code": "910",
            "items": [{"merchant_sku": "S517-0085", "title": "x", "quantity": 1}],
        }], "wait_for_courier": []})

        seen = []
        with patch(
            "backend.state_helpers._save_processed_packaging_orders",
            side_effect=lambda d, sid: (seen.append(dict(d)), self.processed.update(d)),
        ):
            self.run_cycle({"cargo_assembly": [{
                "code": "911",
                "items": [{"merchant_sku": "S517-0054", "title": "y", "quantity": 1}],
            }], "wait_for_courier": []})

        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertTrue(next(iter(seen[0])).startswith("911"))
        # Отметка первого заказа при этом никуда не делась.
        self.assertTrue(any(k.startswith("910") for k in self.processed))

    def test_marks_are_read_by_key_not_by_full_scan(self):
        """Спрашиваем только ключи текущей выборки, а не всю таблицу."""
        asked = []

        def _load(sid, keys=None):
            asked.append(keys)
            return dict(self.processed)

        self.groups = self._three_groups()
        with patch("backend.state_helpers._load_processed_packaging_orders", side_effect=_load):
            self.run_cycle({"cargo_assembly": [{
                "code": "920",
                "items": [{"merchant_sku": "S517-0085", "title": "x", "quantity": 1}],
            }], "wait_for_courier": []})

        self.assertEqual(len(asked), 1)
        self.assertIsNotNone(asked[0])
        self.assertIn("920", asked[0])
        self.assertIn(worker._claim_key("920", "S517-0085"), asked[0])

    def test_claim_lookup_asks_for_three_keys_not_the_table(self):
        """claim_stock_deduction читал всю таблицу броней на каждую позицию."""
        asked = []
        with patch(
            "backend.state_helpers._load_processed_orders_by_kind",
            side_effect=lambda sid, kind, keys=None: (asked.append(keys), {})[1],
        ), patch("backend.state_helpers._save_processed_orders_by_kind", side_effect=lambda d, s, k: None):
            worker.claim_stock_deduction(STORE, "930", "test", {}, sku="S517-0085 2шт")

        self.assertEqual(len(asked), 1)
        self.assertIsNotNone(asked[0])
        self.assertIn(worker._claim_key("930", "S517-0085 2шт"), asked[0])
        self.assertIn(worker._legacy_claim_key("930", "S517-0085 2шт"), asked[0])
        self.assertIn("930", asked[0])


class TestSweepReportsWhichGroups(unittest.TestCase):
    """Сторож возвращает ID поправленных групп, а не голый флаг."""

    def test_returns_the_ids_it_touched(self):
        g = {"status": "deducting", "group_name": "A"}
        self.assertEqual(worker._sweep_stuck_statuses({"a": g}), ["a"])

    def test_returns_empty_when_there_is_nothing_to_fix(self):
        g = {"status": "synced", "group_name": "A"}
        self.assertEqual(worker._sweep_stuck_statuses({"a": g}), [])
