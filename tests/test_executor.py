import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.append(os.getcwd())

from backend.dumper.executor import RateLimitExecutorWorker
from backend.dumper.config import DumpingProductConfig
from backend.services.offer_sync import Priority


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


def make_task(sku="TEST-0001", new_price=990, action="DUMP_TOP_1", **product_overrides):
    config = make_config(sku=sku)
    product = {"name": "Товар", "sku": sku, "merchantSku": sku, "stock": 10}
    product.update(product_overrides)
    return {
        "sku": sku,
        "new_price": new_price,
        "config": config,
        "calculation_result": {
            "bestCompetitorPrice": 995,
            "action": action,
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
    """
    До 2026-08-12 отсутствие кук ТЕРЯЛО решение демпинга: цена писалась только
    в локальный оверрайд, а в Kaspi не уходила уже никогда. Теперь намерение
    ставится в очередь и переживает восстановление сессии.
    """

    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_missing_cookies_still_queues_and_saves_override(self, mock_cookies, mock_groups):
        mock_cookies.return_value = {}
        mock_groups.return_value = {}
        result = RateLimitExecutorWorker.execute_updates(
            [make_task()], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertTrue(any("повторного входа" in m for m in result["logs"]))
        self.save_override.assert_called_once()

    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_incomplete_cookies_treated_as_no_auth(self, mock_cookies, mock_groups):
        mock_cookies.return_value = {"mc-session": "abc"}  # missing mc-sid
        mock_groups.return_value = {}
        result = RateLimitExecutorWorker.execute_updates(
            [make_task()], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertTrue(any("повторного входа" in m for m in result["logs"]))


class TestExecuteUpdatesEnqueues(ExecutorTestBase):
    @patch("backend.services.offer_sync.enqueue")
    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_queues_intent_instead_of_calling_kaspi(self, mock_cookies, mock_groups, mock_enqueue):
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        result = RateLimitExecutorWorker.execute_updates(
            [make_task()], self.save_override, rate_limit_delay=0,
            merchant_id="999", store_id="store-1",
        )
        self.assertEqual(result["updated_count"], 1)
        mock_enqueue.assert_called_once()
        kwargs = mock_enqueue.call_args.kwargs
        self.assertEqual(kwargs["price"], 990)
        self.assertEqual(kwargs["stock"], 10)
        self.assertEqual(mock_enqueue.call_args.args[1], "TEST-0001")

    @patch("backend.services.offer_sync.enqueue")
    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_stop_qty_group_forces_zero_stock_and_p0(self, mock_cookies, mock_groups, mock_enqueue):
        # Остаток группы на "Стоп кол-ве" -> на Kaspi уходит 0, и это P0:
        # иначе Kaspi продолжит продавать товар, которого нет.
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {
            "g1": {"is_active": True, "linked_skus": ["TEST-0001"], "shared_stock": 5, "stop_qty": 5}
        }
        RateLimitExecutorWorker.execute_updates(
            [make_task()], self.save_override, rate_limit_delay=0,
            merchant_id="999", store_id="store-1",
        )
        kwargs = mock_enqueue.call_args.kwargs
        self.assertEqual(kwargs["stock"], 0)
        self.assertEqual(kwargs["priority"], Priority.WITHDRAW)

    @patch("backend.services.offer_sync.enqueue")
    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_price_cut_is_higher_priority_than_pull_up(self, mock_cookies, mock_groups, mock_enqueue):
        mock_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_groups.return_value = {}
        RateLimitExecutorWorker.execute_updates(
            [make_task(new_price=900, action="DUMP_TOP_1")], self.save_override,
            rate_limit_delay=0, merchant_id="999", store_id="store-1",
        )
        self.assertEqual(mock_enqueue.call_args.kwargs["priority"], Priority.LOSING)

        mock_enqueue.reset_mock()
        RateLimitExecutorWorker.execute_updates(
            [make_task(new_price=1100, action="PULL_UP_PRICE")], self.save_override,
            rate_limit_delay=0, merchant_id="999", store_id="store-1",
        )
        self.assertEqual(mock_enqueue.call_args.kwargs["priority"], Priority.MARGIN)


class TestExecuteUpdatesDedup(ExecutorTestBase):
    @patch("backend.state_helpers._load_master_stock_groups")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_duplicate_skus_keep_only_last_occurrence(self, mock_cookies, mock_groups):
        mock_cookies.return_value = {}
        mock_groups.return_value = {}
        task_old = make_task(sku="DUP-1", new_price=900)
        task_new = make_task(sku="DUP-1", new_price=950)
        result = RateLimitExecutorWorker.execute_updates(
            [task_old, task_new], self.save_override, rate_limit_delay=0, merchant_id="999"
        )
        self.assertEqual(result["updated_count"], 1)
        self.save_override.assert_called_once()
        self.assertEqual(self.save_override.call_args.args[1], 950)


class TestExecuteUpdatesLogPersistence(ExecutorTestBase):
    """Слайс 7: _append_dumper_logs требует реальный store_id и пишет в БД —
    изолируемся на временной SQLite, как в test_dumping_configs_store_scoping.py."""

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

    @patch("backend.services.offer_sync.enqueue")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_saved_margin_is_computed_honestly_from_price_diff(self, mock_cookies, _enq):
        mock_cookies.return_value = {}
        task = make_task(new_price=990)
        task["config"] = make_config(min_price=800.0)
        RateLimitExecutorWorker.execute_updates(
            [task], self.save_override, rate_limit_delay=0, merchant_id="999",
            store_id="store-test",
        )
        from backend.state_helpers import _load_dumper_logs
        self.assertEqual(_load_dumper_logs("store-test", limit=5)[0]["saved_margin"], 190)

    @patch("backend.services.offer_sync.enqueue")
    @patch("backend.dumper.executor._load_mc_cookies")
    def test_saved_margin_is_zero_without_min_price_floor(self, mock_cookies, _enq):
        mock_cookies.return_value = {}
        RateLimitExecutorWorker.execute_updates(
            [make_task(new_price=990)], self.save_override, rate_limit_delay=0,
            merchant_id="999", store_id="store-test",
        )
        from backend.state_helpers import _load_dumper_logs
        self.assertEqual(_load_dumper_logs("store-test", limit=5)[0]["saved_margin"], 0)


if __name__ == "__main__":
    unittest.main()
