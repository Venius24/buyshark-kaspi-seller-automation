import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.append(os.getcwd())

from backend.dumper.executor import RateLimitExecutorWorker
from backend.dumper.config import DumpingProductConfig


def make_config(**overrides):
    defaults = dict(
        sku="TEST-0001",
        master_sku="123456",
        my_current_price=1000.0,
        min_price=0.0,
        max_price=0.0,
        step=10.0,
        city_code="750000000",
    )
    defaults.update(overrides)
    return DumpingProductConfig(**defaults)


def make_task(sku="TEST-0001", new_price=990, **product_overrides):
    config = make_config(sku=sku)
    product = {"name": "Товар", "sku": sku, "merchantSku": sku, "stock": 10}
    product.update(product_overrides)
    return {
        "sku": sku,
        "new_price": new_price,
        "config": config,
        "calculation_result": {
            "bestCompetitorPrice": 995,
            "action": "DUMP_TOP_1",
            "currentPos": 1,
        },
        "product_ref": product,
    }


class ExecutorTestBase(unittest.TestCase):
    """Isolates the relative-path 'states/dumper_logs.json' write into a scratch cwd."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        self.save_override = MagicMock()

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)


class TestExecuteUpdatesNoAuth(ExecutorTestBase):
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_missing_cookies_skips_mc_api_but_saves_override(self, mock_cookies):
        mock_cookies.return_value = {}
        task = make_task()
        result = RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertTrue(any("SMS-вход" in m for m in result["logs"]))
        self.save_override.assert_called_once()

    @patch("backend.dumper.executor._load_mc_cookies")
    def test_incomplete_cookies_treated_as_no_auth(self, mock_cookies):
        mock_cookies.return_value = {"mc-session": "abc"}  # missing mc-sid
        task = make_task()
        result = RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertTrue(any("SMS-вход" in m for m in result["logs"]))


class TestExecuteUpdatesWithAuth(ExecutorTestBase):
    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor.RateLimitExecutorWorker._update_kaspi_price_sync")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_success_path_logs_transaction_id(
        self, mock_cookies, mock_update, mock_groups
    ):
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        mock_update.return_value = (True, "tx-999")
        task = make_task()
        result = RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertTrue(any("tx-999" in m for m in result["logs"]))
        self.save_override.assert_called_once()

    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor.RateLimitExecutorWorker._update_kaspi_price_sync")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_failed_update_logs_error_but_still_advances(
        self, mock_cookies, mock_update, mock_groups
    ):
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        mock_update.return_value = (False, "Шаг 1 (Discount) провален со статусом 500")
        task = make_task()
        result = RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertTrue(any("MC API: ✗" in m for m in result["logs"]))

    @patch("time.sleep")
    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor.RateLimitExecutorWorker._update_kaspi_price_sync")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_429_triggers_backoff_then_retries_once(
        self, mock_cookies, mock_update, mock_groups, mock_sleep
    ):
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        mock_update.side_effect = [(False, "429_BLOCKED"), (True, "tx-after-backoff")]
        task = make_task()
        result = RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        mock_sleep.assert_any_call(60)
        self.assertEqual(mock_update.call_count, 2)
        self.assertEqual(result["updated_count"], 1)
        self.assertTrue(any("tx-after-backoff" in m for m in result["logs"]))

    @patch("time.sleep")
    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor.RateLimitExecutorWorker._update_kaspi_price_sync")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_persistent_block_aborts_entire_queue(
        self, mock_cookies, mock_update, mock_groups, mock_sleep
    ):
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        mock_update.return_value = (False, "429_BLOCKED")
        task1 = make_task(sku="SKU-A")
        task2 = make_task(sku="SKU-B")
        result = RateLimitExecutorWorker.execute_updates(
            [task1, task2], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        # First task retried once after backoff, then queue aborted before task2 runs
        self.assertEqual(mock_update.call_count, 2)
        self.assertEqual(result["updated_count"], 0)
        self.assertTrue(any("Останавливаем выполнение очереди" in m for m in result["logs"]))
        self.save_override.assert_not_called()


class TestExecuteUpdatesDedup(ExecutorTestBase):
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_duplicate_skus_keep_only_last_occurrence(self, mock_cookies):
        mock_cookies.return_value = {}
        task_old = make_task(sku="DUP-1", new_price=900)
        task_new = make_task(sku="DUP-1", new_price=950)
        result = RateLimitExecutorWorker.execute_updates(
            [task_old, task_new], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertEqual(result["updated_count"], 1)
        self.save_override.assert_called_once()
        called_price = self.save_override.call_args.args[1]
        self.assertEqual(called_price, 950)


class TestExecuteUpdatesLogPersistence(ExecutorTestBase):
    """Characterizes the current saved_margin placeholder (hardcoded 1500/2500 by
    substring-matching the log message) — a known stub flagged for a real fix in Phase 4."""

    @patch("backend.dumper.executor._load_mc_cookies")
    def test_default_saved_margin_is_hardcoded_1500(self, mock_cookies):
        mock_cookies.return_value = {}
        task = make_task()
        RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        import json
        with open("states/dumper_logs.json", encoding="utf-8") as f:
            logs = json.load(f)
        self.assertEqual(logs[0]["saved_margin"], 1500)

    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor.RateLimitExecutorWorker._update_kaspi_price_sync")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_pull_up_action_bumps_saved_margin_to_2500(
        self, mock_cookies, mock_update, mock_groups
    ):
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        mock_update.return_value = (True, "tx-1")
        task = make_task()
        task["calculation_result"]["action"] = "PULL_UP_PRICE"
        RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        import json
        with open("states/dumper_logs.json", encoding="utf-8") as f:
            logs = json.load(f)
        self.assertEqual(logs[0]["saved_margin"], 2500)


if __name__ == "__main__":
    unittest.main()
