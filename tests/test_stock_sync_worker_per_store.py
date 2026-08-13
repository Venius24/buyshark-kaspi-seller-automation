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

    async def test_none_store_id_is_a_distinct_shared_key(self):
        """store_id=None all share one lock key — not reachable from any
        real call site any more (Слайс 7), but harmless as a dict key."""
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


class TestRunPickupCampaignCycle(unittest.IsolatedAsyncioTestCase):
    """Phase 7: run_pickup_campaign_cycle() — the testable core of
    pickup_campaign_loop(), extracted from the infinite while-loop."""

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_no_active_stores_does_nothing(self, mock_list_stores):
        """Слайс 7: легаси-фоллбек на общую сессию убран — нет активных
        сторов, нечего сканировать."""
        mock_list_stores.return_value = []
        with patch("backend.services.stock_sync_worker._run_pickup_campaign_pass", new_callable=AsyncMock) as mock_pass:
            await worker.run_pickup_campaign_cycle()
        mock_pass.assert_not_awaited()

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_iterates_every_active_store_independently(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch("backend.services.stock_sync_worker._run_pickup_campaign_pass", new_callable=AsyncMock) as mock_pass:
            await worker.run_pickup_campaign_cycle()
        self.assertEqual(mock_pass.await_args_list[0].args, ("tok_a", "store-a"))
        self.assertEqual(mock_pass.await_args_list[1].args, ("tok_b", "store-b"))

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_one_store_failing_does_not_block_the_others(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch(
            "backend.services.stock_sync_worker._run_pickup_campaign_pass",
            new_callable=AsyncMock,
            side_effect=[Exception("Kaspi API 500"), None],
        ) as mock_pass:
            await worker.run_pickup_campaign_cycle()
        self.assertEqual(mock_pass.await_count, 2)


class TestRunPackagingSyncCycle(unittest.IsolatedAsyncioTestCase):
    """Phase 7: run_packaging_sync_cycle() — the testable core of
    start_packaging_sync_loop()."""

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_no_active_stores_does_nothing(self, mock_list_stores):
        """Слайс 7: легаси-фоллбек на общую сессию убран — нет активных
        сторов, нечего списывать."""
        mock_list_stores.return_value = []
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

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_auto_assembly_cycle_runs_for_every_active_store(self, mock_list_stores):
        """Автосборка повешена на тот же 30с тик, что и авто-списание —
        отдельного лупа/опроса Kaspi не заведено (см. HANDOFF §3.0)."""
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch("backend.services.stock_sync_worker.process_packaging_auto_deduction", new_callable=AsyncMock), \
             patch("backend.services.auto_assembly_worker.run_auto_assembly_cycle", new_callable=AsyncMock) as mock_aa:
            await worker.run_packaging_sync_cycle()
        self.assertEqual(mock_aa.await_args_list[0].args, ("tok_a", "store-a"))
        self.assertEqual(mock_aa.await_args_list[1].args, ("tok_b", "store-b"))

    @patch("backend.services.stock_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_auto_assembly_failure_does_not_block_other_stores(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch("backend.services.stock_sync_worker.process_packaging_auto_deduction", new_callable=AsyncMock), \
             patch(
                 "backend.services.auto_assembly_worker.run_auto_assembly_cycle",
                 new_callable=AsyncMock,
                 side_effect=[Exception("boom"), None],
             ) as mock_aa:
            await worker.run_packaging_sync_cycle()
        self.assertEqual(mock_aa.await_count, 2)


class TestExecuteBroadcastSyncStoreScoping(unittest.IsolatedAsyncioTestCase):
    """execute_broadcast_sync() must read/write the group under the same
    store_id it was called with — no cross-store leakage."""

    @patch("backend.services.stock_sync_worker._load_master_stock_groups")
    async def test_group_lookup_is_scoped_by_store_id(self, mock_load):
        mock_load.return_value = {}
        await worker.execute_broadcast_sync("grp_1", "token", store_id="store-a")
        mock_load.assert_called_once_with(store_id="store-a")


if __name__ == "__main__":
    unittest.main()
