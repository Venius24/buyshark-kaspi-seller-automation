import os
import sys
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


class TestApiKeysRouterRequiresAuth(unittest.TestCase):
    """
    Слайс 7: legacy-фоллбек на общую сессию убран целиком — запрос без
    валидного JWT+магазина получает 401 на всех эндпоинтах.
    """

    def setUp(self):
        self.client = TestClient(app)

    def test_get_keys_with_no_auth_header_returns_401(self):
        resp = self.client.get("/api/integrations/keys")
        self.assertEqual(resp.status_code, 401)

    def test_create_key_without_resolved_store_returns_401(self):
        resp = self.client.post("/api/integrations/keys", json={"name": "1C Integration"})
        self.assertEqual(resp.status_code, 401)

    def test_webhook_config_with_no_auth_header_returns_401(self):
        resp = self.client.post("/api/integrations/webhook", json={
            "url": "https://example.com/hook", "secret": "s3cr3t123", "enabled": True,
        })
        self.assertEqual(resp.status_code, 401)


class TestApiKeysRouterNewPath(unittest.TestCase):
    """The new JWT+store path, through the real api_keys.py router — a key
    created under one store must not be visible/revocable from another."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _auth_as_store(self, store_id):
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id=store_id, merchant_id=f"m_{store_id}", name="Shop", encrypted_api_key="cipher")

        fake_db = AsyncMock()
        fake_db.execute.side_effect = [
            make_scalar_result(user), make_scalar_result(store),
        ] * 10  # enough for several dependency-resolution calls across requests

        async def fake_get_db_gen():
            yield fake_db

        from backend.db.database import get_db as real_get_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen
        return {"Authorization": f"Bearer {access_tok}"}

    @patch("backend.security.store_context.decrypt_data", return_value="tok")
    def test_key_created_for_store_a_not_visible_or_revocable_from_store_b(self, mock_decrypt):
        headers_a = self._auth_as_store("store-A")
        try:
            create_resp = self.client.post(
                "/api/integrations/keys", json={"name": "1C Integration"}, headers=headers_a
            )
            self.assertEqual(create_resp.status_code, 200)
            key_id = create_resp.json()["key_id"]

            list_resp_a = self.client.get("/api/integrations/keys", headers=headers_a)
            self.assertEqual(list_resp_a.json()["count"], 1)
        finally:
            from backend.db.database import get_db as real_get_db
            app.dependency_overrides.pop(real_get_db, None)

        headers_b = self._auth_as_store("store-B")
        try:
            list_resp_b = self.client.get("/api/integrations/keys", headers=headers_b)
            self.assertEqual(list_resp_b.json()["count"], 0)

            revoke_resp_b = self.client.delete(f"/api/integrations/keys/{key_id}", headers=headers_b)
            self.assertEqual(revoke_resp_b.status_code, 404)
        finally:
            from backend.db.database import get_db as real_get_db
            app.dependency_overrides.pop(real_get_db, None)

        headers_a2 = self._auth_as_store("store-A")
        try:
            revoke_resp_a = self.client.delete(f"/api/integrations/keys/{key_id}", headers=headers_a2)
            self.assertEqual(revoke_resp_a.status_code, 200)
        finally:
            from backend.db.database import get_db as real_get_db
            app.dependency_overrides.pop(real_get_db, None)


if __name__ == "__main__":
    unittest.main()
