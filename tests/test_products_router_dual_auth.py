import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from tests.store_context_stub import install_store_context_db

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


def make_store_role_result(store, role="OWNER", allowed_sections=None):
    """_resolve_store_for_user selects (Store, UserStore.role,
    UserStore.allowed_sections) and reads the row via .first() — a plain
    make_scalar_result() only wires .scalar_one_or_none()/.scalars().first().
    allowed_sections=None (JSON-текст в БД) = «ограничений по разделам нет»,
    см. backend/security/sections.py."""
    result = MagicMock()
    result.first.return_value = (store, role, allowed_sections)
    return result


class TestProductsRouterRequiresAuth(unittest.TestCase):
    """
    Слайс 7: legacy-фоллбек на общую сессию убран целиком — запрос без
    валидного JWT+магазина получает 401 на всех эндпоинтах, а не тихий
    доступ к общей/чужой сессии.
    """

    def setUp(self):
        self.client = TestClient(app)

    def test_get_products_with_no_auth_header_returns_401(self):
        resp = self.client.get("/api/products")
        self.assertEqual(resp.status_code, 401)

    def test_update_product_with_no_auth_header_returns_401(self):
        resp = self.client.post("/api/products/update", json={
            "sku": "PROD-1", "price": 1500, "stock": 20,
        })
        self.assertEqual(resp.status_code, 401)

    def test_refresh_products_with_no_auth_header_returns_401(self):
        resp = self.client.post("/api/products/refresh")
        self.assertEqual(resp.status_code, 401)

    def test_feed_with_no_auth_header_returns_401(self):
        resp = self.client.get("/api/feed")
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
            make_scalar_result(user), make_store_role_result(store),
            make_scalar_result(user), make_store_role_result(store),
        ]

        async def fake_get_db_gen():
            yield fake_db

        from backend.db.database import get_db as real_get_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen
        # get_store_context держит свою сессию, не Depends(get_db) — см. store_context_stub.
        install_store_context_db(self, fake_db)
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
