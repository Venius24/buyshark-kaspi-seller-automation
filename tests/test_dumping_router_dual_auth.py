import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.security.auth import create_access_token
from backend.db.models import User, Store


def make_scalar_result(value):
    from unittest.mock import MagicMock
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    scalars_result = MagicMock()
    scalars_result.first.return_value = value
    result.scalars.return_value = scalars_result
    return result


class TestDumpingRouterRequiresAuth(unittest.TestCase):
    """
    Слайс 7: legacy-фоллбек на общую сессию убран целиком — запрос без
    валидного JWT+магазина получает 401, а не тихий доступ к общей/чужой сессии.
    """

    def setUp(self):
        self.client = TestClient(app)

    def test_get_configs_with_no_auth_header_returns_401(self):
        resp = self.client.get("/api/dumping/configs")
        self.assertEqual(resp.status_code, 401)

    def test_save_config_with_no_auth_header_returns_401(self):
        resp = self.client.post("/api/dumping/config", json={
            "sku": "PILOT-SKU-1",
            "min_price": 100,
            "max_price": 500,
            "dump_step": 10,
            "enabled": True,
        })
        self.assertEqual(resp.status_code, 401)


class TestDumpingRouterNewPath(unittest.TestCase):
    """The new JWT+store path — not yet used by the real frontend, but must
    work end-to-end through the actual router for when it is."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)
        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch("backend.security.store_context.decrypt_data")
    def test_jwt_path_scopes_config_to_resolved_store(self, mock_decrypt):
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-1", merchant_id="m_new", name="Shop", encrypted_api_key="cipher")
        mock_decrypt.return_value = "new_path_token"

        from unittest.mock import AsyncMock
        fake_db = AsyncMock()
        # Two HTTP requests below, each triggering get_store_context once
        # (one User lookup + one Store lookup per call) = 4 execute() calls.
        fake_db.execute.side_effect = [
            make_scalar_result(user), make_scalar_result(store),
            make_scalar_result(user), make_scalar_result(store),
        ]

        async def fake_get_db_gen():
            yield fake_db

        # get_store_context takes db via Depends(get_db); override the FastAPI
        # dependency directly rather than patching the get_db symbol (which
        # dependency-injection already resolved by import time in main.py).
        from backend.db.database import get_db as real_get_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen
        try:
            resp = self.client.post(
                "/api/dumping/config",
                json={"sku": "NEW-PATH-SKU", "min_price": 1, "max_price": 2, "dump_step": 1, "enabled": True},
                headers={"Authorization": f"Bearer {access_tok}"},
            )
            self.assertEqual(resp.status_code, 200)

            get_resp = self.client.get(
                "/api/dumping/configs", headers={"Authorization": f"Bearer {access_tok}"}
            )
            self.assertIn("NEW-PATH-SKU", get_resp.json()["configs"])
        finally:
            app.dependency_overrides.pop(real_get_db, None)


if __name__ == "__main__":
    unittest.main()
