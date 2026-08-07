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
        self.assertTrue(any("повторный вход" in m for m in result["logs"]))
        self.save_override.assert_called_once()

    @patch("backend.dumper.executor._load_mc_cookies")
    def test_incomplete_cookies_treated_as_no_auth(self, mock_cookies):
        mock_cookies.return_value = {"mc-session": "abc"}  # missing mc-sid
        task = make_task()
        result = RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertTrue(any("повторный вход" in m for m in result["logs"]))


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
        # Категория 1: execute_updates теперь обрабатывает очередь через
        # ThreadPoolExecutor (по умолчанию max_workers=3). max_workers=1 здесь
        # воспроизводит строго последовательный порядок, чтобы детерминированно
        # проверить "abort останавливает СТАРТ следующей задачи" — с реальной
        # конкурентностью (>1) SKU-B мог бы успеть стартовать до того, как
        # SKU-A выставит abort_event, и это не баг, а осознанный компромисс
        # параллельности (см. test_concurrent_skus_run_in_parallel ниже).
        result = RateLimitExecutorWorker.execute_updates(
            [task1, task2], self.save_override, rate_limit_delay=0, merchant_id="999",
            max_workers=1,
        )
        # First task retried once after backoff, then queue aborted before task2 runs
        self.assertEqual(mock_update.call_count, 2)
        self.assertEqual(result["updated_count"], 0)
        self.assertTrue(any("Останавливаем выполнение очереди" in m for m in result["logs"]))
        self.save_override.assert_not_called()

    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor.RateLimitExecutorWorker._update_kaspi_price_sync")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_concurrent_skus_run_in_parallel(self, mock_cookies, mock_update, mock_groups):
        """
        Категория 1: N independent SKUs must actually overlap in wall-clock
        time under the default max_workers=3, not just "not crash". Each
        mocked Kaspi call sleeps for real (simulating network latency) —
        if execute_updates were still sequential, 6 SKUs would take
        >= 6 * SLEEP seconds; concurrent execution with 3 workers should
        take roughly 2 batches worth (well under the sequential total).
        """
        import time as time_module

        SLEEP = 0.15
        NUM_SKUS = 6

        def slow_update(*args, **kwargs):
            time_module.sleep(SLEEP)
            return (True, "tx-ok")

        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        mock_update.side_effect = slow_update

        tasks = [make_task(sku=f"SKU-{i}") for i in range(NUM_SKUS)]
        start = time_module.perf_counter()
        result = RateLimitExecutorWorker.execute_updates(
            tasks, self.save_override, rate_limit_delay=0, merchant_id="999", max_workers=3
        )
        elapsed = time_module.perf_counter() - start

        self.assertEqual(result["updated_count"], NUM_SKUS)
        sequential_estimate = NUM_SKUS * SLEEP
        # Real concurrency: well under sequential time. Generous margin (0.7x)
        # to absorb scheduling/CI jitter while still failing hard for the
        # old fully-sequential implementation (which would take ~1.0x).
        self.assertLess(elapsed, sequential_estimate * 0.7)


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
    substring-matching the log message) — a known stub flagged for a real fix in Phase 4.
    Слайс 7: _append_dumper_logs now requires a real store_id and writes to the DB,
    not states/dumper_logs.json — isolate against a scratch SQLite DB, same pattern as
    test_dumping_configs_store_scoping.py."""

    def setUp(self):
        super().setUp()
        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine

        db_path = os.path.join(self._tmp_dir, "executor_test.db")
        self._sync_engine = create_engine(
            f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        from sqlalchemy.orm import Session
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

    def tearDown(self):
        self._session_patch.stop()
        super().tearDown()

    @patch("backend.dumper.executor._load_mc_cookies")
    def test_saved_margin_is_computed_honestly_from_price_diff(self, mock_cookies):
        # Раньше saved_margin была захардкожена (1500/2500 ₸ по совпадению
        # подстроки в тексте лога) — теперь это реальная разница new_price - min_price.
        mock_cookies.return_value = {}
        task = make_task(new_price=990)
        task["config"] = make_config(min_price=800.0)
        RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999",
            store_id="store-test",
        )
        from backend.state_helpers import _load_dumper_logs
        logs = _load_dumper_logs("store-test", limit=5)
        self.assertEqual(logs[0]["saved_margin"], 190)

    def test_saved_margin_is_zero_without_min_price_floor(self):
        # Без настроенной минималки честная маржа — 0, а не выдуманное число.
        task = make_task(new_price=990)
        RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999",
            store_id="store-test",
        )
        from backend.state_helpers import _load_dumper_logs
        logs = _load_dumper_logs("store-test", limit=5)
        self.assertEqual(logs[0]["saved_margin"], 0)

    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor.RateLimitExecutorWorker._update_kaspi_price_sync")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_pull_up_action_saved_margin_matches_price_diff(
        self, mock_cookies, mock_update, mock_groups
    ):
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        mock_update.return_value = (True, "tx-1")
        task = make_task(new_price=990)
        task["config"] = make_config(min_price=800.0)
        task["calculation_result"]["action"] = "PULL_UP_PRICE"
        RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999",
            store_id="store-test",
        )
        from backend.state_helpers import _load_dumper_logs
        logs = _load_dumper_logs("store-test", limit=5)
        self.assertEqual(logs[0]["saved_margin"], 190)


if __name__ == "__main__":
    unittest.main()
