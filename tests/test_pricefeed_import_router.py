import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import pricefeed_import as pfi
from backend.security.store_context import StoreContext, get_store_context_billed
from backend.services import pricefeed_client


def _ctx(store_id):
    async def _override():
        return StoreContext(token="tok", merchant_id="m1", store_id=store_id)
    return _override


def _fetch_result(items, merchant_id="20186986", merchant_name="BARKHAN", merchants=None):
    merchant = pricefeed_client.PriceFeedMerchant(id=merchant_id, name=merchant_name)
    return pricefeed_client.PriceFeedFetchResult(
        merchant=merchant,
        merchants=merchants or [merchant],
        items=items,
        pages_fetched=1,
        truncated=False,
    )


class PriceFeedRouterTestBase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        os.environ["PRICEFEED_IMPORT_ENABLED"] = "1"
        pfi._PENDING.clear()
        pfi._LAST_IMPORT_AT.clear()

    def tearDown(self):
        os.environ.pop("PRICEFEED_IMPORT_ENABLED", None)
        app.dependency_overrides.pop(get_store_context_billed, None)
        pfi._PENDING.clear()
        pfi._LAST_IMPORT_AT.clear()


class TestAuthAndFlag(PriceFeedRouterTestBase):
    def test_preview_without_auth_returns_401(self):
        resp = self.client.post("/api/dumping/pricefeed/preview", json={"phone": "+7", "password": "x"})
        self.assertEqual(resp.status_code, 401)

    def test_apply_without_auth_returns_401(self):
        resp = self.client.post("/api/dumping/pricefeed/apply", json={"import_id": "nope"})
        self.assertEqual(resp.status_code, 401)

    def test_disabled_by_default(self):
        os.environ.pop("PRICEFEED_IMPORT_ENABLED", None)
        self.assertFalse(pfi._pricefeed_import_enabled())

    def test_preview_returns_503_when_disabled(self):
        os.environ.pop("PRICEFEED_IMPORT_ENABLED", None)
        app.dependency_overrides[get_store_context_billed] = _ctx("store-1")
        resp = self.client.post(
            "/api/dumping/pricefeed/preview", json={"phone": "+7", "password": "x"}
        )
        self.assertEqual(resp.status_code, 503)


class TestPreviewFlow(PriceFeedRouterTestBase):
    def setUp(self):
        super().setUp()
        app.dependency_overrides[get_store_context_billed] = _ctx("store-1")
        self.products = [{"sku": "X1", "merchantSku": "X1"}]

    @patch("backend.routers.pricefeed_import._load_dumping_configs", return_value={})
    @patch("backend.routers.pricefeed_import._load_products")
    @patch.object(pricefeed_client, "fetch_pricefeed_settings")
    def test_preview_ready_returns_import_id_and_sample(self, mock_fetch, mock_products, mock_configs):
        mock_products.return_value = self.products
        mock_fetch.return_value = _fetch_result([{
            "sku": "X1", "masterSku": "1", "masterTitle": "T",
            "algorithm": "BEST_PRICE_MINUS_STEP", "minPrice": 100, "maxPrice": 500, "step": 2,
        }])

        resp = self.client.post(
            "/api/dumping/pricefeed/preview", json={"phone": "+77001234567", "password": "secret-pw-1"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ready")
        self.assertIn("import_id", data)
        self.assertEqual(data["importable"], 1)
        self.assertIn(data["import_id"], pfi._PENDING)

    @patch.object(pricefeed_client, "fetch_pricefeed_settings")
    def test_preview_multiple_merchants_asks_to_choose(self, mock_fetch):
        m1 = pricefeed_client.PriceFeedMerchant(id="1", name="Shop A")
        m2 = pricefeed_client.PriceFeedMerchant(id="2", name="Shop B")
        mock_fetch.return_value = pricefeed_client.PriceFeedFetchResult(
            merchant=None, merchants=[m1, m2], items=[], pages_fetched=1,
        )
        resp = self.client.post(
            "/api/dumping/pricefeed/preview", json={"phone": "+7", "password": "x"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "choose_merchant")
        self.assertNotIn("import_id", data)

    @patch.object(pricefeed_client, "fetch_pricefeed_settings")
    def test_auth_error_returns_401_without_leaking_password(self, mock_fetch):
        mock_fetch.side_effect = pricefeed_client.PriceFeedAuthError(
            "Не удалось войти в PriceFeed. Проверьте номер телефона и пароль."
        )
        secret_password = "sUp3rSecretPW"
        resp = self.client.post(
            "/api/dumping/pricefeed/preview", json={"phone": "+7", "password": secret_password}
        )
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn(secret_password, resp.text)

    @patch.object(pricefeed_client, "fetch_pricefeed_settings")
    def test_rate_limited_returns_429(self, mock_fetch):
        mock_fetch.side_effect = pricefeed_client.PriceFeedRateLimited("квота исчерпана")
        resp = self.client.post(
            "/api/dumping/pricefeed/preview", json={"phone": "+7", "password": "x"}
        )
        self.assertEqual(resp.status_code, 429)

    @patch("backend.routers.pricefeed_import._load_dumping_configs", return_value={})
    @patch("backend.routers.pricefeed_import._load_products", return_value=[])
    @patch.object(pricefeed_client, "fetch_pricefeed_settings")
    def test_second_preview_within_cooldown_returns_429(self, mock_fetch, mock_products, mock_configs):
        mock_fetch.return_value = _fetch_result([])
        first = self.client.post(
            "/api/dumping/pricefeed/preview", json={"phone": "+7", "password": "x"}
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/api/dumping/pricefeed/preview", json={"phone": "+7", "password": "x"}
        )
        self.assertEqual(second.status_code, 429)


class TestApplyFlow(PriceFeedRouterTestBase):
    def _seed_pending(self, store_id="store-1", rows=None):
        import time
        entry = pfi._PendingImport(
            store_id=store_id,
            created_at=time.monotonic(),
            merchant={"id": "1", "name": "Shop"},
            rows=rows if rows is not None else {"X1": {
                "min_price": 100, "max_price": 500, "dump_step": 2, "enabled": True, "mode": "TOP_1",
                "target_position": 1, "max_delivery_days": None, "min_seller_rating": None,
                "same_city_only": False, "blacklisted_merchants": [], "city_code": "750000000",
                "sku": "X1", "_source_title": "T",
            }},
            report={},
        )
        pfi._PENDING["import-1"] = entry
        return entry

    @patch("backend.routers.pricefeed_import._save_dumping_configs")
    @patch("backend.routers.pricefeed_import._load_dumping_configs", return_value={})
    def test_apply_creates_new_config(self, mock_load, mock_save):
        app.dependency_overrides[get_store_context_billed] = _ctx("store-1")
        self._seed_pending(store_id="store-1")

        resp = self.client.post("/api/dumping/pricefeed/apply", json={"import_id": "import-1"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["created"], 1)
        self.assertEqual(data["updated"], 0)
        mock_save.assert_called_once()
        written = mock_save.call_args[0][0]
        self.assertIn("X1", written)
        self.assertNotIn("_source_title", written["X1"])
        self.assertNotIn("import-1", pfi._PENDING)

    @patch("backend.routers.pricefeed_import._save_dumping_configs")
    @patch("backend.routers.pricefeed_import._load_dumping_configs")
    def test_apply_without_overwrite_skips_existing(self, mock_load, mock_save):
        app.dependency_overrides[get_store_context_billed] = _ctx("store-1")
        mock_load.return_value = {"X1": {"min_price": 1, "max_price": 2, "city_code": "999"}}
        self._seed_pending(store_id="store-1")

        resp = self.client.post(
            "/api/dumping/pricefeed/apply", json={"import_id": "import-1", "overwrite": False}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["skipped_existing"], 1)
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["updated"], 0)
        mock_save.assert_not_called()

    @patch("backend.routers.pricefeed_import._save_dumping_configs")
    @patch("backend.routers.pricefeed_import._load_dumping_configs")
    def test_apply_with_overwrite_merges_and_preserves_extra_fields(self, mock_load, mock_save):
        app.dependency_overrides[get_store_context_billed] = _ctx("store-1")
        mock_load.return_value = {
            "X1": {
                "min_price": 1, "max_price": 2, "dump_step": 1, "enabled": True, "mode": "TOP_1",
                "target_position": 7, "max_delivery_days": 3, "min_seller_rating": 4.0,
                "same_city_only": True, "blacklisted_merchants": ["evil-merchant"], "city_code": "999",
            }
        }
        self._seed_pending(store_id="store-1")

        resp = self.client.post(
            "/api/dumping/pricefeed/apply", json={"import_id": "import-1", "overwrite": True}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["updated"], 1)
        written = mock_save.call_args[0][0]["X1"]
        self.assertEqual(written["min_price"], 100)
        self.assertEqual(written["city_code"], "999")
        self.assertEqual(written["blacklisted_merchants"], ["evil-merchant"])
        self.assertEqual(written["target_position"], 7)

    def test_apply_unknown_import_id_returns_410(self):
        app.dependency_overrides[get_store_context_billed] = _ctx("store-1")
        resp = self.client.post("/api/dumping/pricefeed/apply", json={"import_id": "does-not-exist"})
        self.assertEqual(resp.status_code, 410)

    @patch("backend.routers.pricefeed_import._save_dumping_configs")
    def test_apply_with_other_stores_import_id_returns_404_and_writes_nothing(self, mock_save):
        self._seed_pending(store_id="store-A")
        app.dependency_overrides[get_store_context_billed] = _ctx("store-B")

        resp = self.client.post("/api/dumping/pricefeed/apply", json={"import_id": "import-1"})
        self.assertEqual(resp.status_code, 404)
        mock_save.assert_not_called()
        # Pending entry must survive an unauthorized attempt, not be consumed.
        self.assertIn("import-1", pfi._PENDING)


if __name__ == "__main__":
    unittest.main()
