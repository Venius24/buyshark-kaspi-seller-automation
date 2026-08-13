import os
import sys
import datetime
import unittest
from unittest.mock import patch, AsyncMock, call

sys.path.append(os.getcwd())

from backend.security.store_context import StoreContext
from backend.services import products_sync_worker as worker


class TestRunProductsSyncCycle(unittest.IsolatedAsyncioTestCase):
    """Phase 7: run_products_sync_cycle() — the testable core of
    start_products_sync_loop(), extracted from the infinite while-loop."""

    @patch("backend.services.products_sync_worker._get_token")
    @patch("backend.services.products_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_no_stores_and_no_legacy_token_does_nothing(self, mock_list_stores, mock_get_token):
        mock_list_stores.return_value = []
        mock_get_token.return_value = None
        with patch("backend.services.products_sync_worker._sync_store_products", new_callable=AsyncMock) as mock_sync:
            await worker.run_products_sync_cycle()
        mock_sync.assert_not_awaited()

    @patch("backend.services.products_sync_worker._get_token")
    @patch("backend.services.products_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_no_stores_in_db_falls_back_to_legacy_session(self, mock_list_stores, mock_get_token):
        mock_list_stores.return_value = []
        mock_get_token.return_value = "legacy_token"
        with patch("backend.services.products_sync_worker._sync_store_products", new_callable=AsyncMock) as mock_sync:
            await worker.run_products_sync_cycle()
        mock_sync.assert_awaited_once_with("legacy_token", None)

    @patch("backend.services.products_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_single_store_is_synced_with_its_own_token_and_store_id(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
        ]
        with patch("backend.services.products_sync_worker._sync_store_products", new_callable=AsyncMock) as mock_sync:
            await worker.run_products_sync_cycle()
        mock_sync.assert_awaited_once_with("tok_a", "store-a")

    @patch("backend.services.products_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_multiple_stores_are_each_synced_independently(self, mock_list_stores):
        """Слайс 1: каталог теперь per-store (StoreProductDB) — каждый
        активный магазин синхронизируется в свой собственный кэш, больше не
        только первый по порядку создания."""
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        with patch("backend.services.products_sync_worker._sync_store_products", new_callable=AsyncMock) as mock_sync:
            await worker.run_products_sync_cycle()
        self.assertEqual(
            mock_sync.await_args_list,
            [call("tok_a", "store-a"), call("tok_b", "store-b")],
        )

    @patch("backend.services.products_sync_worker.list_active_stores", new_callable=AsyncMock)
    async def test_sync_failure_is_caught_not_raised(self, mock_list_stores):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
        ]
        with patch(
            "backend.services.products_sync_worker._sync_store_products",
            new_callable=AsyncMock,
            side_effect=Exception("Kaspi API down"),
        ):
            await worker.run_products_sync_cycle()  # must not raise


class TestSyncStoreProducts(unittest.IsolatedAsyncioTestCase):
    """_sync_store_products() — the actual Kaspi fetch + overrides + per-store cache write."""

    @patch("backend.services.products_sync_worker._apply_overrides")
    @patch("backend.services.products_sync_worker.KaspiAPI.sync_merchant_assortment")
    async def test_empty_catalog_skips_write_returns_zero(self, mock_fetch, mock_overrides):
        mock_fetch.return_value = []
        count = await worker._sync_store_products("tok", "store-a")
        self.assertEqual(count, 0)
        mock_overrides.assert_not_called()

    @patch("backend.services.products_sync_worker._save_products")
    @patch("backend.services.products_sync_worker._apply_overrides")
    @patch("backend.services.products_sync_worker.KaspiAPI.sync_merchant_assortment")
    async def test_overrides_applied_and_saved_with_the_given_store_id(self, mock_fetch, mock_overrides, mock_save):
        mock_fetch.return_value = [{"sku": "X1"}]
        mock_overrides.return_value = [{"sku": "X1", "price_num": 500}]

        count = await worker._sync_store_products("tok", "store-a")

        args, kwargs = mock_overrides.call_args
        self.assertEqual(args, ([{"sku": "X1"}],))
        self.assertEqual(kwargs["store_id"], "store-a")
        # Барьер свежести обязателен: без него на только что выкачанные из
        # Kaspi цены ложатся протухшие оверрайды (см. tests/test_overrides_freshness.py).
        self.assertIsInstance(kwargs["fresh_from_kaspi_at"], datetime.datetime)
        mock_save.assert_called_once_with([{"sku": "X1", "price_num": 500}], "store-a")
        self.assertEqual(count, 1)

    @patch("backend.services.products_sync_worker._save_products")
    @patch("backend.services.products_sync_worker._apply_overrides")
    @patch("backend.services.products_sync_worker.KaspiAPI.sync_merchant_assortment")
    async def test_none_store_id_preserves_legacy_unscoped_overrides(self, mock_fetch, mock_overrides, mock_save):
        mock_fetch.return_value = [{"sku": "X1"}]
        mock_overrides.return_value = [{"sku": "X1"}]

        await worker._sync_store_products("tok")

        args, kwargs = mock_overrides.call_args
        self.assertEqual(args, ([{"sku": "X1"}],))
        self.assertIsNone(kwargs["store_id"])
        mock_save.assert_called_once_with([{"sku": "X1"}], None)


if __name__ == "__main__":
    unittest.main()
