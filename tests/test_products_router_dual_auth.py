import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.security.auth import create_access_token
from backend.db.models import User, Store


def make_scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    scalars_result = MagicMock()
    scalars_result.first.return_value = value
    result.scalars.return_value = scalars_result
    return result


class TestProductsRouterLegacyPath(unittest.TestCase):
    """
    End-to-end check that migrating products.py to the dual-auth dependency
    preserves EXACTLY today's behavior for real production traffic, which
    sends no JWT at all — highest money-impact router in the plan, so this
    is verified against the real router, not just the underlying helpers.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)
        with open("states/session.json", "w", encoding="utf-8") as f:
            json.dump({"token": "test_token_pilot", "merchant_id": "m_test"}, f)
        with open("states/products.json", "w", encoding="utf-8") as f:
            json.dump([{
                "sku": "PROD-1", "name": "Test Product", "price": "1000 ₸",
                "status": "Активен", "merchantSku": "PROD-1", "kaspiSku": "111",
            }], f)
        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_get_products_with_no_auth_header_uses_legacy_session(self):
        resp = self.client.get("/api/products")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["total_count"], 1)
        self.assertEqual(body["products"][0]["sku"], "PROD-1")

    def test_update_product_with_no_auth_header_uses_legacy_session(self):
        resp = self.client.post("/api/products/update", json={
            "sku": "PROD-1", "price": 1500, "stock": 20,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        get_resp = self.client.get("/api/products")
        updated = get_resp.json()["products"][0]
        self.assertEqual(updated["price_num"], 1500)
        self.assertEqual(updated["stock"], 20)

    def test_missing_legacy_session_returns_401_same_as_before(self):
        os.remove("states/session.json")
        resp = self.client.get("/api/products")
        self.assertEqual(resp.status_code, 401)


class TestProductsRouterNewPath(unittest.TestCase):
    """The new JWT+store path, through the real products.py router."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        # Слайс 1: каталог теперь per-store (StoreProductDB), не общий файл —
        # засеиваем ИМЕННО кэш store-1 через тот же _save_products, который
        # использует сам роутер, а не общий states/products.json.
        from backend import state_helpers
        state_helpers._save_products([{
            "sku": "PROD-2", "name": "Test Product 2", "price": "2000 ₸",
            "status": "Активен", "merchantSku": "PROD-2", "kaspiSku": "222",
        }], store_id="store-1")

        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch("backend.security.store_context.decrypt_data")
    def test_jwt_path_scopes_override_to_resolved_store(self, mock_decrypt):
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-1", merchant_id="m_new", name="Shop", encrypted_api_key="cipher")
        mock_decrypt.return_value = "new_path_token"

        fake_db = AsyncMock()
        fake_db.execute.side_effect = [
            make_scalar_result(user), make_scalar_result(store),
            make_scalar_result(user), make_scalar_result(store),
        ]

        async def fake_get_db_gen():
            yield fake_db

        from backend.db.database import get_db as real_get_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen
        try:
            resp = self.client.post(
                "/api/products/update",
                json={"sku": "PROD-2", "price": 3000, "stock": 15},
                headers={"Authorization": f"Bearer {access_tok}"},
            )
            self.assertEqual(resp.status_code, 200)

            get_resp = self.client.get(
                "/api/products", headers={"Authorization": f"Bearer {access_tok}"}
            )
            product = get_resp.json()["products"][0]
            self.assertEqual(product["price_num"], 3000)
        finally:
            app.dependency_overrides.pop(real_get_db, None)


if __name__ == "__main__":
    unittest.main()
