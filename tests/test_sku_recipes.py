"""
Рецепт списания артикула: ×N на карточке и наборы из нескольких Мастер-Групп
(2026-09-27, backend/services/sku_recipes.py).

Живые случаи Бархана, из которых выросла задача:
- `S517-0046-48` «Набор для ремонта» = съёмник S517-0046 + ключ S517-0048.
  Лежал только в группе 46: продажа не трогала 48, а на витрине у набора
  висел остаток съёмников (~329) при 88 ключах.
- «H422-0007 1шт» и «H422-0007 2 шт» — две группы на одну полку, потому
  что множитель был только на всю группу.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(os.getcwd())

from backend.services import sku_recipes as R
from backend.services import stock_sync_worker as worker
from tests.test_packaging_deduction_hardening import DeductionTestCase, group
from tests.test_stock_groups_edit_and_delete import StockGroupsRouterTestCase, make_group


def g(gid, name, skus, stock=100, stop=0, recipes=None, active=True):
    out = group(gid, name, skus, stock=stock)
    out["stop_qty"] = stop
    out["is_active"] = active
    if recipes:
        out["sku_recipes"] = recipes
    return out


BUNDLE = {"S517-0046-48": {"qty": 1, "extra": [{"group_id": "g48", "qty": 1}]}}


def bundle_groups(stock46=317, stock48=88, stop46=0, stop48=0):
    return {
        "g46": g("g46", "S517-0046 Съёмник", ["S517-0046", "S517-0046-48"], stock=stock46,
                 stop=stop46, recipes={k: {**v, "extra": [dict(x) for x in v["extra"]]} for k, v in BUNDLE.items()}),
        "g48": g("g48", "S517-0048 Ключ", ["S517-0048"], stock=stock48, stop=stop48),
    }


# ── Чистые правила ──────────────────────────────────────────────────────────

class TestComponents(unittest.TestCase):
    def test_no_recipe_is_own_group_times_one(self):
        self.assertEqual(R.components_for("g48", bundle_groups(), "S517-0048"), [("g48", 1)])

    def test_bundle_lists_own_group_first(self):
        self.assertEqual(
            R.components_for("g46", bundle_groups(), "S517-0046-48"), [("g46", 1), ("g48", 1)]
        )

    def test_annotated_sku_is_a_different_card(self):
        """«H422-0007-9 2шт» и «H422-0007-9» — разные товары (§3.49)."""
        gr = {"a": g("a", "H422-0007", ["H422-0007-9", "H422-0007-9 2шт"],
                     recipes={"H422-0007-9 2шт": {"qty": 2}})}
        self.assertEqual(R.components_for("a", gr, "H422-0007-9 2шт"), [("a", 2)])
        self.assertEqual(R.components_for("a", gr, "H422-0007-9"), [("a", 1)])

    def test_key_matches_regardless_of_case_and_spaces(self):
        gr = {"a": g("a", "A", ["X-1"], recipes={"X-1": {"qty": 3}})}
        self.assertEqual(R.components_for("a", gr, "  x-1 "), [("a", 3)])


class TestSellable(unittest.TestCase):
    def test_plain_sku_keeps_the_old_rule(self):
        gr = {"a": g("a", "A", ["X"], stock=10, stop=3)}
        self.assertEqual(R.sellable([("a", 1)], gr), (10, 10))
        gr["a"]["shared_stock"] = 3
        self.assertEqual(R.sellable([("a", 1)], gr), (3, 0))

    def test_double_pack_shows_half(self):
        gr = {"a": g("a", "A", ["X"], stock=317)}
        self.assertEqual(R.sellable([("a", 2)], gr), (158, 158))

    def test_double_pack_with_one_left_is_not_for_sale(self):
        gr = {"a": g("a", "A", ["X"], stock=1)}
        self.assertEqual(R.sellable([("a", 2)], gr), (0, 0))

    def test_bundle_is_limited_by_the_poorest_component(self):
        self.assertEqual(R.sellable([("g46", 1), ("g48", 1)], bundle_groups()), (88, 88))

    def test_bundle_is_withdrawn_when_any_component_hits_stop(self):
        """Решение владельца 27.09: одна составляющая на стопе — набор снят."""
        gr = bundle_groups(stock48=2, stop48=2)
        self.assertEqual(R.sellable([("g46", 1), ("g48", 1)], gr), (2, 0))

    def test_disabled_component_does_not_limit(self):
        gr = bundle_groups(stock48=0)
        gr["g48"]["is_active"] = False
        self.assertEqual(R.sellable([("g46", 1), ("g48", 1)], gr), (317, 317))


class TestBundlesTouching(unittest.TestCase):
    def test_component_group_sees_the_foreign_bundle(self):
        self.assertEqual(R.bundles_touching("g48", bundle_groups()), [("g46", "S517-0046-48")])

    def test_home_group_does_not_list_itself(self):
        self.assertEqual(R.bundles_touching("g46", bundle_groups()), [])


class TestCleanRecipes(unittest.TestCase):
    def test_default_entries_are_not_stored(self):
        gr = bundle_groups()
        out = R.clean_recipes("g46", {"S517-0046": {"qty": 1}}, gr, ["S517-0046"])
        self.assertEqual(out, {})

    def test_unlinked_sku_is_dropped(self):
        out = R.clean_recipes("g46", {"Z-1": {"qty": 2}}, bundle_groups(), ["S517-0046"])
        self.assertEqual(out, {})

    def test_unknown_group_is_rejected(self):
        with self.assertRaises(ValueError):
            R.clean_recipes("g46", {"S517-0046-48": {"extra": [{"group_id": "nope"}]}},
                            bundle_groups(), ["S517-0046-48"])

    def test_own_group_as_extra_is_rejected(self):
        with self.assertRaises(ValueError):
            R.clean_recipes("g46", {"S517-0046-48": {"extra": [{"group_id": "g46"}]}},
                            bundle_groups(), ["S517-0046-48"])

    def test_zero_qty_is_rejected(self):
        with self.assertRaises(ValueError):
            R.clean_recipes("g46", {"S517-0046": {"qty": 0}}, bundle_groups(), ["S517-0046"])

    def test_lenient_mode_drops_dangling_component(self):
        out = R.clean_recipes("g46", {"S517-0046-48": {"qty": 1, "extra": [{"group_id": "gone"}]}},
                              bundle_groups(), ["S517-0046-48"], strict=False)
        self.assertEqual(out, {})

    def test_drop_group_refs(self):
        gr = bundle_groups()
        changed = R.drop_group_refs(gr, "g48")
        self.assertEqual(list(changed), ["g46"])
        self.assertEqual(gr["g46"]["sku_recipes"], {})


# ── Списание по заказу ──────────────────────────────────────────────────────

def order(code, sku, qty=1):
    return {"cargo_assembly": [{"code": code, "items": [
        {"merchant_sku": sku, "title": "t", "quantity": qty}]}], "wait_for_courier": []}


class TestBundleDeduction(DeductionTestCase):
    def test_bundle_sale_deducts_every_component(self):
        self.groups = bundle_groups()
        res = self.run_cycle(order("901", "S517-0046-48", qty=2))
        self.assertEqual(self.groups["g46"]["shared_stock"], 315)
        self.assertEqual(self.groups["g48"]["shared_stock"], 86)
        self.assertEqual(res["deducted_orders_count"], 1)
        self.assertEqual({c["group_id"] for c in res["deductions"][0]["components"]}, {"g46", "g48"})

    def test_both_groups_are_broadcast(self):
        self.groups = bundle_groups()
        self.run_cycle(order("902", "S517-0046-48"))
        self.assertEqual({gid for gid, _ in self.broadcasts}, {"g46", "g48"})

    def test_component_journal_names_the_bundle(self):
        self.groups = bundle_groups()
        self.run_cycle(order("903", "S517-0046-48"))
        self.assertIn("набор", self.groups["g48"]["last_sync_log"])
        self.assertIn("S517-0046-48", self.groups["g48"]["last_sync_log"])

    def test_bundle_is_deducted_once(self):
        self.groups = bundle_groups()
        self.run_cycle(order("904", "S517-0046-48"))
        self.run_cycle(order("904", "S517-0046-48"))
        self.assertEqual(self.groups["g48"]["shared_stock"], 87)

    def test_plain_sale_of_the_key_does_not_touch_the_puller(self):
        self.groups = bundle_groups()
        self.run_cycle(order("905", "S517-0048"))
        self.assertEqual(self.groups["g46"]["shared_stock"], 317)
        self.assertEqual(self.groups["g48"]["shared_stock"], 87)

    def test_disabled_component_is_skipped_and_explained(self):
        self.groups = bundle_groups()
        self.groups["g48"]["is_active"] = False
        self.run_cycle(order("906", "S517-0046-48"))
        self.assertEqual(self.groups["g46"]["shared_stock"], 316)
        self.assertEqual(self.groups["g48"]["shared_stock"], 88)
        self.assertTrue(any("выключена или удалена" in l for l in self.groups["g46"]["sync_logs"]))

    def test_component_oversell_goes_into_its_own_debt(self):
        self.groups = bundle_groups(stock48=0)
        with patch.object(worker, "notify_oversold_telegram", new_callable=AsyncMock):
            self.run_cycle(order("907", "S517-0046-48"))
        self.assertEqual(self.groups["g48"]["oversold"], 1)
        self.assertEqual(self.groups["g46"].get("oversold", 0), 0)

    def test_mark_keeps_home_numbers_at_the_top(self):
        self.groups = bundle_groups()
        self.run_cycle(order("908", "S517-0046-48"))
        mark = self.processed[worker._claim_key("908", "S517-0046-48")]
        self.assertEqual(mark["group_id"], "g46")
        self.assertEqual((mark["old_stock"], mark["new_stock"]), (317, 316))
        self.assertEqual(len(mark["components"]), 2)


class TestPackOnCard(DeductionTestCase):
    def test_only_the_double_card_deducts_two(self):
        """Одна группа H422-0007 вместо двух: одиночная и двойная карточки."""
        self.groups = {"a": g("a", "H422-0007", ["H422-0007-3", "H422-0007-9 2шт"], stock=100,
                              recipes={"H422-0007-9 2шт": {"qty": 2}})}
        self.run_cycle({"cargo_assembly": [{"code": "910", "items": [
            {"merchant_sku": "H422-0007-3", "title": "t", "quantity": 1},
            {"merchant_sku": "H422-0007-9 2шт", "title": "t", "quantity": 1},
        ]}], "wait_for_courier": []})
        self.assertEqual(self.groups["a"]["shared_stock"], 97)


# ── Рассылка остатка на Kaspi ───────────────────────────────────────────────

PRODUCTS = [
    {"sku": "1046", "merchantSku": "S517-0046", "kaspiSku": "1046", "name": "Съёмник", "price_num": 1000},
    {"sku": "1468", "merchantSku": "S517-0046-48", "kaspiSku": "1468", "name": "Набор", "price_num": 2000},
    {"sku": "1048", "merchantSku": "S517-0048", "kaspiSku": "1048", "name": "Ключ", "price_num": 900},
    {"sku": "1070", "merchantSku": "H422-0007-9 2шт", "kaspiSku": "1070", "name": "2 шт", "price_num": 900},
]


class BroadcastCase(unittest.IsolatedAsyncioTestCase):
    async def _run(self, groups, gid):
        cm = [
            patch.object(worker, "_load_products", return_value=[dict(p) for p in PRODUCTS]),
            patch.object(worker, "_load_master_stock_groups", return_value=groups),
            patch.object(worker, "_save_master_stock_groups"),
            patch.object(worker, "_save_products"),
            patch.object(worker, "_save_override"),
            patch.object(worker, "discover_merchant_id", return_value="20186986"),
            patch.object(worker, "broadcast_ws_event", new_callable=AsyncMock),
            patch.object(worker, "notify_zero_stock_telegram", new_callable=AsyncMock),
        ]
        for p in cm:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in cm])
        with patch.object(worker.offer_sync, "enqueue") as enq, \
             patch.object(worker.offer_sync, "last_known_stock", return_value=None):
            await worker._execute_broadcast_sync_impl(gid, "tok", store_id="store-a")
        return {c.args[1]: c.kwargs["stock"] for c in enq.call_args_list}

    async def test_bundle_shows_the_poorest_component(self):
        sent = await self._run(bundle_groups(), "g46")
        self.assertEqual(sent["S517-0046"], 317)
        self.assertEqual(sent["S517-0046-48"], 88)

    async def test_component_group_republishes_the_foreign_bundle(self):
        """Кончились ключи — набор должен уйти с витрины, хотя он в группе 46."""
        sent = await self._run(bundle_groups(stock48=0), "g48")
        self.assertEqual(sent, {"S517-0048": 0, "S517-0046-48": 0})

    async def test_double_pack_shows_half(self):
        groups = {"a": g("a", "H422-0007", ["H422-0007-9 2шт"], stock=9,
                         recipes={"H422-0007-9 2шт": {"qty": 2}})}
        sent = await self._run(groups, "a")
        self.assertEqual(sent["H422-0007-9 2шт"], 4)


# ── Роутер ──────────────────────────────────────────────────────────────────

class TestRouterRecipes(StockGroupsRouterTestCase):
    def _seed_pair(self):
        a = make_group("grp_A")
        a["linked_skus"] = ["S517-0046", "S517-0046-48"]
        b = make_group("grp_B")
        b["linked_skus"] = ["S517-0048"]
        self._seed(a)
        self._seed(b)

    def test_bundle_is_saved_and_reloaded(self):
        self._seed_pair()
        resp = self.client.put("/api/stock-groups/grp_A", json={"sku_recipes": {
            "S517-0046-48": {"qty": 1, "extra": [{"group_id": "grp_B", "qty": 1}]},
        }})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._reload("grp_A")["sku_recipes"], {
            "S517-0046-48": {"qty": 1, "extra": [{"group_id": "grp_B", "qty": 1}]},
        })

    def test_unknown_component_group_is_400(self):
        self._seed_pair()
        resp = self.client.put("/api/stock-groups/grp_A", json={"sku_recipes": {
            "S517-0046-48": {"extra": [{"group_id": "grp_NOPE", "qty": 1}]},
        }})
        self.assertEqual(resp.status_code, 400)

    def test_other_edits_keep_the_recipe(self):
        self._seed_pair()
        self.client.put("/api/stock-groups/grp_A", json={"sku_recipes": {"S517-0046": {"qty": 2}}})
        self.client.put("/api/stock-groups/grp_A", json={"shared_stock": 10})
        self.assertEqual(self._reload("grp_A")["sku_recipes"], {"S517-0046": {"qty": 2}})

    def test_unlinking_a_sku_drops_its_recipe(self):
        self._seed_pair()
        self.client.put("/api/stock-groups/grp_A", json={"sku_recipes": {"S517-0046-48": {"qty": 2}}})
        self.client.put("/api/stock-groups/grp_A", json={"linked_skus": ["S517-0046"]})
        self.assertEqual(self._reload("grp_A")["sku_recipes"], {})

    def test_deleting_a_component_group_cleans_the_bundle(self):
        self._seed_pair()
        self.client.put("/api/stock-groups/grp_A", json={"sku_recipes": {
            "S517-0046-48": {"qty": 1, "extra": [{"group_id": "grp_B", "qty": 1}]},
        }})
        self.client.delete("/api/stock-groups/grp_B")
        self.assertEqual(self._reload("grp_A")["sku_recipes"], {})


# ── Прогноз ─────────────────────────────────────────────────────────────────

def test_forecast_counts_foreign_bundle_sales_against_the_component():
    from datetime import date, timedelta
    from backend.services.stock_forecast import build_forecast

    today = date(2026, 9, 27)
    daily = {s: {today - timedelta(days=d): 1 for d in range(1, 31)}
             for s in ("S517-0046-48", "S517-0048")}
    groups = bundle_groups()
    for x in groups.values():
        x["external_code"] = None
    res = build_forecast(
        products=[{"sku": p["sku"], "merchantSku": p["merchantSku"], "name": p["name"],
                   "stock": 10, "status": "Активен", "price_num": 1} for p in PRODUCTS[:3]],
        groups=groups,
        daily_by_sku=daily,
        today=today,
    )
    rows = {it["key"]: it for it in res["items"]}
    # Ключи уходят и поштучно (1/день), и в наборе (1/день).
    assert rows["g48"]["velocity"] == pytest.approx(2.0, rel=0.05)
    assert rows["g46"]["velocity"] == pytest.approx(1.0, rel=0.05)
