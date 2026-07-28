import os
import sys
import asyncio
import unittest
from unittest.mock import patch, AsyncMock

sys.path.append(os.getcwd())

from backend.security.store_context import StoreContext
from backend.services import stock_sync_worker as worker


class TestGetStockLock(unittest.IsolatedAsyncioTestCase):
    """Phase 7: the single global asyncio.Lock() became a per-store dict of
    locks, so store A's sync no longer blocks on store B's lock."""

    def setUp(self):
        worker._stock_locks.clear()

    async def test_same_store_id_returns_the_same_lock(self):
        lock_a1 = worker.get_stock_lock("store-a")
        lock_a2 = worker.get_stock_lock("store-a")
        self.assertIs(lock_a1, lock_a2)

    async def test_different_store_ids_get_different_locks(self):
        lock_a = worker.get_stock_lock("store-a")
        lock_b = worker.get_stock_lock("store-b")
        self.assertIsNot(lock_a, lock_b)

    async def test_none_store_id_is_the_legacy_shared_key(self):
        """store_id=None (unscoped/legacy callers) all share one lock — the
        same contract the single global asyncio.Lock() used to provide."""
        lock1 = worker.get_stock_lock(None)
        lock2 = worker.get_stock_lock()
        self.assertIs(lock1, lock2)

    async def test_one_store_locked_does_not_block_another_store(self):
        lock_a = worker.get_stock_lock("store-a")
        lock_b = worker.get_stock_lock("store-b")
        await lock_a.acquire()
        try:
            # Must not block/deadlock — different store, different lock.
            acquired = lock_b.locked()
            self.assertFalse(acquired)
            await asyncio.wait_for(lock_b.acquire(), timeout=1)
            lock_b.release()
        finally:
            lock_a.release()


class TestRunStockRadarCycle(unittest.IsolatedAsyncioTestCase):
    """Phase 7: run_stock_radar_cycle() — the testable core of
    master_stock_radar_loop(), extracted from the infinite while-loop."""

    @patch("backend.services.stock_sync_worker._get_token")
    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_no_stores_and_no_legacy_token_does_nothing(self, mock_list_stores, mock_get_token):
        mock_list_stores.return_value = []
        mock_get_token.return_value = None
        with patch("backend.services.stock_sync_worker._run_stock_radar_pass", new_callable=AsyncMock) as mock_pass:
            await worker.run_stock_radar_cycle()
        mock_pass.assert_not_awaited()

    @patch("backend.services.stock_sync_worker._get_token")
    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_no_stores_in_db_falls_back_to_legacy_session(self, mock_list_stores, mock_get_token):
        mock_list_stores.return_value = []
        mock_get_token.return_value = "legacy_token"
        with patch("backend.services.stock_sync_worker._run_stock_radar_pass", new_callable=AsyncMock) as mock_pass:
            await worker.run_stock_radar_cycle()
        mock_pass.assert_awaited_once_with("legacy_token", None)

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_iterates_every_active_store_independently(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch("backend.services.stock_sync_worker._run_stock_radar_pass", new_callable=AsyncMock) as mock_pass:
            await worker.run_stock_radar_cycle()
        self.assertEqual(mock_pass.await_args_list[0].args, ("tok_a", "store-a"))
        self.assertEqual(mock_pass.await_args_list[1].args, ("tok_b", "store-b"))

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_one_store_failing_does_not_block_the_others(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch(
            "backend.services.stock_sync_worker._run_stock_radar_pass",
            new_callable=AsyncMock,
            side_effect=[Exception("Kaspi API 500"), None],
        ) as mock_pass:
            await worker.run_stock_radar_cycle()
        self.assertEqual(mock_pass.await_count, 2)


class TestRunPackagingSyncCycle(unittest.IsolatedAsyncioTestCase):
    """Phase 7: run_packaging_sync_cycle() — the testable core of
    start_packaging_sync_loop()."""

    @patch("backend.services.stock_sync_worker._get_token")
    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_no_stores_in_db_falls_back_to_legacy_session(self, mock_list_stores, mock_get_token):
        mock_list_stores.return_value = []
        mock_get_token.return_value = "legacy_token"
        with patch("backend.services.stock_sync_worker.process_packaging_auto_deduction", new_callable=AsyncMock) as mock_dedup:
            await worker.run_packaging_sync_cycle()
        mock_dedup.assert_awaited_once_with("legacy_token")

    @patch("backend.services.stock_sync_worker._get_token")
    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_legacy_test_token_is_skipped(self, mock_list_stores, mock_get_token):
        """Characterizes the pre-existing dev/test guard: a token starting
        with 'test_' must not trigger a real Kaspi API call."""
        mock_list_stores.return_value = []
        mock_get_token.return_value = "test_fake_token"
        with patch("backend.services.stock_sync_worker.process_packaging_auto_deduction", new_callable=AsyncMock) as mock_dedup:
            await worker.run_packaging_sync_cycle()
        mock_dedup.assert_not_awaited()

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_iterates_every_active_store_independently(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch("backend.services.stock_sync_worker.process_packaging_auto_deduction", new_callable=AsyncMock) as mock_dedup:
            await worker.run_packaging_sync_cycle()
        self.assertEqual(mock_dedup.await_args_list[0].args, ("tok_a", "store-a"))
        self.assertEqual(mock_dedup.await_args_list[1].args, ("tok_b", "store-b"))

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_one_store_failing_does_not_block_the_others(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch(
            "backend.services.stock_sync_worker.process_packaging_auto_deduction",
            new_callable=AsyncMock,
            side_effect=[Exception("Kaspi API 500"), {"success": True}],
        ) as mock_dedup:
            await worker.run_packaging_sync_cycle()
        self.assertEqual(mock_dedup.await_count, 2)


class TestExecuteBroadcastSyncStoreScoping(unittest.IsolatedAsyncioTestCase):
    """execute_broadcast_sync() must read/write the group under the same
    store_id it was called with — no cross-store leakage."""

    @patch("backend.services.stock_sync_worker._load_master_stock_groups")
    async def test_group_lookup_is_scoped_by_store_id(self, mock_load):
        mock_load.return_value = {}
        await worker.execute_broadcast_sync("grp_1", "token", store_id="store-a")
        mock_load.assert_called_once_with(store_id="store-a")

    @patch("backend.services.stock_sync_worker._load_master_stock_groups")
    async def test_none_store_id_preserves_legacy_unscoped_read(self, mock_load):
        mock_load.return_value = {}
        await worker.execute_broadcast_sync("grp_1", "token")
        mock_load.assert_called_once_with(store_id=None)


if __name__ == "__main__":
    unittest.main()
