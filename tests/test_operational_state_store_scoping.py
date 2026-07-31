import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class OperationalStateStoreScopingTest(unittest.TestCase):
    """
    Слайс 5: processed_orders (radar/packaging)/print_history/dumper_logs/
    analytics_snapshots были общими файлами на все магазины. Эти тесты —
    доказательство, что каждый теперь изолирован per-store.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "operational_scoping_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # --- processed_orders (radar) ---

    def test_processed_orders_radar_isolated_between_stores(self):
        self.state_helpers._save_processed_orders({"order-1": {"processed_at": "t"}}, store_id="store-A")
        self.assertIn("order-1", self.state_helpers._load_processed_orders(store_id="store-A"))
        self.assertNotIn("order-1", self.state_helpers._load_processed_orders(store_id="store-B"))

    def test_processed_orders_radar_and_packaging_are_independent_kinds(self):
        self.state_helpers._save_processed_orders({"order-1": {"processed_at": "t"}}, store_id="store-A")
        self.state_helpers._save_processed_packaging_orders({"order-1": {"deducted_at": "t"}}, store_id="store-A")

        radar = self.state_helpers._load_processed_orders(store_id="store-A")
        packaging = self.state_helpers._load_processed_packaging_orders(store_id="store-A")
        self.assertEqual(radar["order-1"], {"processed_at": "t"})
        self.assertEqual(packaging["order-1"], {"deducted_at": "t"})

    def test_processed_packaging_orders_isolated_between_stores(self):
        self.state_helpers._save_processed_packaging_orders({"order-9": {"deducted_at": "t"}}, store_id="store-A")
        self.assertNotIn("order-9", self.state_helpers._load_processed_packaging_orders(store_id="store-B"))

    # --- print history ---

    def test_print_history_isolated_between_stores(self):
        from backend.services.print_batcher_service import load_printed_history, save_printed_history

        save_printed_history({"order-key-1"}, store_id="store-A", date_str="2026-08-01")
        self.assertIn("order-key-1", load_printed_history(store_id="store-A", date_str="2026-08-01"))
        self.assertNotIn("order-key-1", load_printed_history(store_id="store-B", date_str="2026-08-01"))

    def test_print_history_reset_only_affects_its_own_store(self):
        from backend.services.print_batcher_service import load_printed_history, save_printed_history, reset_printed_history

        save_printed_history({"k1"}, store_id="store-A", date_str="2026-08-01")
        save_printed_history({"k2"}, store_id="store-B", date_str="2026-08-01")

        reset_printed_history(store_id="store-A", date_str="2026-08-01")

        self.assertEqual(load_printed_history(store_id="store-A", date_str="2026-08-01"), set())
        self.assertEqual(load_printed_history(store_id="store-B", date_str="2026-08-01"), {"k2"})

    # --- dumper logs ---

    def test_dumper_logs_isolated_between_stores(self):
        self.state_helpers._append_dumper_logs([{"message": "A dumped"}], store_id="store-A")
        self.state_helpers._append_dumper_logs([{"message": "B dumped"}], store_id="store-B")

        logs_a = self.state_helpers._load_dumper_logs(store_id="store-A")
        logs_b = self.state_helpers._load_dumper_logs(store_id="store-B")
        self.assertEqual([e["message"] for e in logs_a], ["A dumped"])
        self.assertEqual([e["message"] for e in logs_b], ["B dumped"])

    def test_dumper_logs_respects_limit_and_recency_order(self):
        for i in range(10):
            self.state_helpers._append_dumper_logs([{"message": f"event-{i}"}], store_id="store-A")

        logs = self.state_helpers._load_dumper_logs(store_id="store-A", limit=3)
        self.assertEqual(len(logs), 3)
        self.assertEqual(logs[0]["message"], "event-9")  # most recent first

    # --- analytics snapshot ---

    def test_analytics_snapshot_isolated_between_stores(self):
        self.state_helpers._save_analytics_snapshot({"total_scanned": 5}, store_id="store-A")
        self.state_helpers._save_analytics_snapshot({"total_scanned": 99}, store_id="store-B")

        self.assertEqual(self.state_helpers._load_analytics_snapshot(store_id="store-A")["total_scanned"], 5)
        self.assertEqual(self.state_helpers._load_analytics_snapshot(store_id="store-B")["total_scanned"], 99)


class DumpingSchedulerStateScopingTest(unittest.TestCase):
    """Слайс 5: last_run_timestamp теперь per-store — ручной запуск одного
    магазина больше не показывает "выполнено только что" другому."""

    def setUp(self):
        from backend.routers import dumping
        self.dumping = dumping
        dumping._scheduler_states.clear()

    def test_last_run_timestamp_independent_per_store(self):
        state_a = self.dumping._get_scheduler_state("store-A")
        state_a.last_run_timestamp = 12345.0

        state_b = self.dumping._get_scheduler_state("store-B")
        self.assertEqual(state_b.last_run_timestamp, 0.0)
        self.assertEqual(self.dumping._get_scheduler_state("store-A").last_run_timestamp, 12345.0)

    def test_is_running_independent_per_store(self):
        self.dumping._get_scheduler_state("store-A").is_running = True
        self.assertFalse(self.dumping._get_scheduler_state("store-B").is_running)


if __name__ == "__main__":
    unittest.main()
