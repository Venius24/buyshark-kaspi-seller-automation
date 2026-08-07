"""Тесты на оркестрацию вокруг движка правил (auto_assembly_worker.py):
загрузка правил из БД, дедуп повторяющихся SKIP-решений в логе, matching
остатка по Мастер-Группам, и что для сторов без единого включённого
правила ни один Kaspi-эндпоинт не дёргается (см. HANDOFF §3.0)."""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

sys.path.append(os.getcwd())

from backend.db.models import Store, AutoAssemblyRuleDB, AutoAssemblyLogDB, WaybillSpoolDB
from backend.services import auto_assembly_worker as worker


class BaseDbTestCase(unittest.IsolatedAsyncioTestCase):
    """Каждый тест — свежая SQLite-БД в изолированном tempdir (тот же
    паттерн, что и tests/test_stores_v1_no_fallback.py)."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(Store(id="store-1", merchant_id="m_store1", name="TestShop"))
        session.commit()
        session.close()

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _add_rule(self, **overrides):
        from backend.state_helpers import _get_sync_db_session
        defaults = dict(
            store_id="store-1", delivery_type="Express", enabled=True, dry_run=True,
            stage_accept=True, stage_assemble=True, stage_waybill=True,
            schedule=None, max_order_price=None, sku_blacklist="[]",
            respect_stock_groups=True, max_per_cycle=None,
        )
        defaults.update(overrides)
        session = _get_sync_db_session()
        session.add(AutoAssemblyRuleDB(**defaults))
        session.commit()
        session.close()


class TestLoadRules(BaseDbTestCase):
    async def test_no_rules_returns_empty_dict(self):
        rules = worker._load_rules("store-1")
        self.assertEqual(rules, {})

    async def test_loads_rule_with_parsed_json_fields(self):
        self._add_rule(
            delivery_type="Postomat",
            schedule='{"start": "09:00", "end": "19:00"}',
            sku_blacklist='["SKU-1", "SKU-2"]',
        )
        rules = worker._load_rules("store-1")
        self.assertIn("Postomat", rules)
        rule = rules["Postomat"]
        self.assertEqual(rule.schedule, {"start": "09:00", "end": "19:00"})
        self.assertEqual(rule.sku_blacklist, ["SKU-1", "SKU-2"])

    async def test_rules_scoped_by_store(self):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(Store(id="store-2", merchant_id="m_store2", name="OtherShop"))
        session.commit()
        session.close()
        self._add_rule(store_id="store-2", delivery_type="Express")

        rules = worker._load_rules("store-1")
        self.assertEqual(rules, {})


class TestStockOk(unittest.TestCase):
    def test_no_groups_means_ok(self):
        order = {"entries": [{"sku": "SKU-1"}]}
        self.assertTrue(worker._stock_ok(order, {}))

    def test_sku_not_linked_to_any_group_means_ok(self):
        order = {"entries": [{"sku": "UNRELATED-SKU"}]}
        groups = {"g1": {"linked_skus": ["sku-1"], "shared_stock": 0}}
        self.assertTrue(worker._stock_ok(order, groups))

    def test_linked_group_with_zero_stock_blocks(self):
        order = {"entries": [{"sku": "SKU-1"}]}
        groups = {"g1": {"linked_skus": ["sku-1"], "shared_stock": 0}}
        self.assertFalse(worker._stock_ok(order, groups))

    def test_linked_group_with_positive_stock_ok(self):
        order = {"entries": [{"sku": "SKU-1"}]}
        groups = {"g1": {"linked_skus": ["sku-1"], "shared_stock": 5}}
        self.assertTrue(worker._stock_ok(order, groups))


class TestLogDedup(BaseDbTestCase):
    async def test_repeated_identical_skip_is_not_duplicated(self):
        worker._log("store-1", "100999", "accept", "SKIP_PRICE_LIMIT", "too expensive", dedupe_skips=True)
        worker._log("store-1", "100999", "accept", "SKIP_PRICE_LIMIT", "too expensive", dedupe_skips=True)

        from backend.state_helpers import _get_sync_db_session
        from sqlalchemy import select
        session = _get_sync_db_session()
        rows = session.scalars(select(AutoAssemblyLogDB)).all()
        session.close()
        self.assertEqual(len(rows), 1)

    async def test_decision_change_is_logged_again(self):
        worker._log("store-1", "100999", "accept", "SKIP_PRICE_LIMIT", "too expensive", dedupe_skips=True)
        worker._log("store-1", "100999", "accept", "SKIP_SKU_BLACKLIST", "blacklisted", dedupe_skips=True)

        from backend.state_helpers import _get_sync_db_session
        from sqlalchemy import select
        session = _get_sync_db_session()
        rows = session.scalars(select(AutoAssemblyLogDB)).all()
        session.close()
        self.assertEqual(len(rows), 2)

    async def test_done_decisions_are_never_deduped(self):
        worker._log("store-1", "100999", "accept", "DONE", "Заказ принят")
        worker._log("store-1", "100999", "accept", "DONE", "Заказ принят")

        from backend.state_helpers import _get_sync_db_session
        from sqlalchemy import select
        session = _get_sync_db_session()
        rows = session.scalars(select(AutoAssemblyLogDB)).all()
        session.close()
        self.assertEqual(len(rows), 2)


class TestLoadExcludedOrderCodes(BaseDbTestCase):
    async def test_window_skips_are_permanently_excluded(self):
        worker._log("store-1", "100111", "window", "SKIP_OUT_OF_WINDOW", "вне окна")
        excluded = worker._load_excluded_order_codes("store-1")
        self.assertEqual(excluded, {"100111"})

    async def test_non_window_skips_do_not_exclude(self):
        worker._log("store-1", "100111", "accept", "SKIP_PRICE_LIMIT", "too expensive")
        excluded = worker._load_excluded_order_codes("store-1")
        self.assertEqual(excluded, set())


class TestRunAutoAssemblyCycleEarlyExit(BaseDbTestCase):
    """Сторы, никогда не касавшиеся Автосборки, не должны порождать ни
    одного лишнего запроса к Kaspi — правил в БД нет вовсе."""

    async def test_no_rules_at_all_skips_kaspi_calls_entirely(self):
        with patch("backend.kaspi_wrapper.KaspiAPI.get_orders_with_products") as mock_fetch:
            await worker.run_auto_assembly_cycle("fake-token", "store-1")
        mock_fetch.assert_not_called()

    async def test_all_rules_disabled_skips_kaspi_calls_entirely(self):
        self._add_rule(delivery_type="Express", enabled=False)
        with patch("backend.kaspi_wrapper.KaspiAPI.get_orders_with_products") as mock_fetch:
            await worker.run_auto_assembly_cycle("fake-token", "store-1")
        mock_fetch.assert_not_called()

    async def test_enabled_rule_with_no_stages_still_fetches_nothing(self):
        """enabled=True, но ни одна из 3 стадий не включена — нечего
        забирать ни на одном из трёх фетчей."""
        self._add_rule(stage_accept=False, stage_assemble=False, stage_waybill=False)
        with patch("backend.kaspi_wrapper.KaspiAPI.get_orders_with_products") as mock_fetch:
            await worker.run_auto_assembly_cycle("fake-token", "store-1")
        mock_fetch.assert_not_called()

    async def test_enabled_accept_stage_fetches_only_new_orders(self):
        self._add_rule(stage_accept=True, stage_assemble=False, stage_waybill=False)
        with patch("backend.kaspi_wrapper.KaspiAPI.get_orders_with_products", return_value=[]) as mock_fetch:
            await worker.run_auto_assembly_cycle("fake-token", "store-1")
        mock_fetch.assert_called_once_with("fake-token", "NEW", "APPROVED_BY_BANK", 50)


class TestSpool(BaseDbTestCase):
    def _add_spool_row(self, order_code, printed_at=None, content=b"%PDF-1.4 fake"):
        from backend.state_helpers import _get_sync_db_session
        import datetime as _dt

        file_dir = worker.SPOOL_DIR / "store-1"
        file_dir.mkdir(parents=True, exist_ok=True)
        file_path = file_dir / f"{order_code}.pdf"
        file_path.write_bytes(content)

        session = _get_sync_db_session()
        session.add(WaybillSpoolDB(
            store_id="store-1", order_code=order_code, date="2026-08-07",
            file_path=str(file_path), downloaded_at=_dt.datetime.now(), printed_at=printed_at,
        ))
        session.commit()
        session.close()
        return file_path

    async def test_get_unprinted_spool_rows_excludes_printed(self):
        import datetime as _dt
        self._add_spool_row("100111")
        self._add_spool_row("100222", printed_at=_dt.datetime.now())

        rows = worker.get_unprinted_spool_rows("store-1")
        self.assertEqual([r["order_code"] for r in rows], ["100111"])

    async def test_build_spool_zip_bytes_contains_unprinted_pdfs(self):
        import zipfile
        import io

        self._add_spool_row("100111")
        self._add_spool_row("100222")

        zip_bytes = worker.build_spool_zip_bytes("store-1")
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = set(zf.namelist())
        self.assertEqual(names, {"100111.pdf", "100222.pdf"})

    async def test_mark_spool_printed_updates_rows_and_returns_count(self):
        self._add_spool_row("100111")
        self._add_spool_row("100222")

        count = worker.mark_spool_printed("store-1")
        self.assertEqual(count, 2)
        self.assertEqual(worker.get_unprinted_spool_rows("store-1"), [])

    async def test_mark_spool_printed_writes_print_history(self):
        import datetime as _dt
        from backend.services.print_batcher_service import load_printed_history

        self._add_spool_row("100111")
        worker.mark_spool_printed("store-1")

        today_str = _dt.datetime.now().strftime("%Y-%m-%d")
        history = load_printed_history(store_id="store-1", date_str=today_str)
        self.assertIn("order_100111", history)

    async def test_mark_spool_printed_with_explicit_order_codes_only_marks_those(self):
        self._add_spool_row("100111")
        self._add_spool_row("100222")

        worker.mark_spool_printed("store-1", order_codes=["100111"])
        remaining = {r["order_code"] for r in worker.get_unprinted_spool_rows("store-1")}
        self.assertEqual(remaining, {"100222"})


if __name__ == "__main__":
    unittest.main()
