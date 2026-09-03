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


class TestBroadcastSyncNeverInventsAnOffer(unittest.IsolatedAsyncioTestCase):
    """Веерная синхронизация раньше подставляла заглушку для SKU, которого нет
    в кэше ассортимента: merchantSku=masterSku и цена 1000 ₸. Намерение уходило
    в очередь под числовым masterSku, а флашер ищет оффер в BFF по merchantSku и
    не находит — сверка вечно возвращает INCOMPLETE. На проде так застряли 16
    остатков Бархана: девять дней в очереди, ноль отправок."""

    GROUP = {
        "group_id": "grp_1",
        "group_name": "Палочки",
        "linked_skus": ["176712278", "D844-0026-1"],
        "shared_stock": 40,
        "stop_qty": 5,
    }

    def _patches(self, products, groups=None):
        groups = groups if groups is not None else {"grp_1": dict(self.GROUP)}
        return [
            patch.object(worker, "_load_products", return_value=products),
            patch.object(worker, "_load_master_stock_groups", return_value=groups),
            patch.object(worker, "_save_master_stock_groups"),
            patch.object(worker, "_save_products"),
            patch.object(worker, "_save_override"),
            patch.object(worker, "discover_merchant_id", return_value="20186986"),
            patch.object(worker, "broadcast_ws_event", new_callable=AsyncMock),
        ]

    async def _run(self, products):
        cm = self._patches(products)
        for p in cm:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in cm])
        with patch.object(worker.offer_sync, "enqueue") as enq, \
             patch.object(worker.offer_sync, "last_known_stock", return_value=None), \
             patch.object(worker.offer_sync, "should_push_stock", return_value=True):
            await worker._execute_broadcast_sync_impl("grp_1", "tok", store_id="store-a")
        return enq

    async def test_unknown_sku_is_skipped_not_invented(self):
        known = {"sku": "198780339", "merchantSku": "D844-0026-1",
                 "kaspiSku": "198780339", "name": "Ложка", "price_num": 1666}
        enq = await self._run([known])
        sent = [c.args[1] for c in enq.call_args_list]
        self.assertEqual(sent, ["D844-0026-1"])
        self.assertNotIn("176712278", sent)

    async def test_empty_product_cache_aborts_instead_of_poisoning_the_queue(self):
        """Пустой кэш — это «не знаем ничего», а не «всё новое»."""
        enq = await self._run([])
        enq.assert_not_called()


class TestStopQtyNotifiesOnlyOnTransition(unittest.IsolatedAsyncioTestCase):
    """До фикса Телеграм-тревога о стоп-кол-ве стреляла на КАЖДЫЙ вызов
    рассылки, пока остаток оставался <= stop_qty — то есть на каждый
    следующий заказ по уже остановленной группе, а не только на переход
    через порог (жалоба 2026-09-03: "уведомления даже по поводу снятых из
    мастер-группы"). Теперь флаг stop_notified — персистентный, per-group."""

    def _patches(self, groups):
        return [
            patch.object(worker, "_load_products", return_value=[
                {"sku": "s1", "merchantSku": "SKU1", "kaspiSku": "s1",
                 "name": "Товар", "price_num": 1000},
            ]),
            patch.object(worker, "_load_master_stock_groups", return_value=groups),
            patch.object(worker, "_save_master_stock_groups"),
            patch.object(worker, "_save_products"),
            patch.object(worker, "_save_override"),
            patch.object(worker, "discover_merchant_id", return_value="20186986"),
            patch.object(worker, "broadcast_ws_event", new_callable=AsyncMock),
        ]

    async def _run(self, shared_stock, stop_notified):
        groups = {"grp_1": {
            "group_id": "grp_1", "group_name": "Палочки", "linked_skus": ["SKU1"],
            "shared_stock": shared_stock, "stop_qty": 5, "stop_notified": stop_notified,
        }}
        cm = self._patches(groups)
        for p in cm:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in cm])
        with patch.object(worker.offer_sync, "enqueue"), \
             patch.object(worker.offer_sync, "last_known_stock", return_value=None), \
             patch.object(worker.offer_sync, "should_push_stock", return_value=True), \
             patch.object(worker, "notify_zero_stock_telegram", new_callable=AsyncMock) as notify:
            await worker._execute_broadcast_sync_impl("grp_1", "tok", store_id="store-a")
        return notify

    async def test_first_drop_below_stop_qty_notifies(self):
        notify = await self._run(shared_stock=0, stop_notified=False)
        notify.assert_awaited_once()

    async def test_staying_below_stop_qty_does_not_renotify(self):
        notify = await self._run(shared_stock=0, stop_notified=True)
        notify.assert_not_called()

    async def test_restock_above_stop_qty_does_not_notify(self):
        notify = await self._run(shared_stock=40, stop_notified=False)
        notify.assert_not_called()
