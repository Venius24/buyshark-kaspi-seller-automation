import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient
from starlette.datastructures import Headers

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


class TestDumpingRouterLegacyPath(unittest.TestCase):
    """
    End-to-end check that migrating dumping.py to the dual-auth dependency
    (Phase 4d pilot) preserves EXACTLY today's behavior for real production
    traffic, which sends no JWT/X-Store-Id at all — the legacy fallback path
    inside get_store_context must be indistinguishable from the old
    _require_token()-only behavior.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)
        with open("states/session.json", "w", encoding="utf-8") as f:
            json.dump({"token": "test_token_pilot", "merchant_id": "m_test"}, f)
        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_get_configs_with_no_auth_header_uses_legacy_session(self):
        resp = self.client.get("/api/dumping/configs")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_save_config_with_no_auth_header_uses_legacy_session(self):
        resp = self.client.post("/api/dumping/config", json={
            "sku": "PILOT-SKU-1",
            "min_price": 100,
            "max_price": 500,
            "dump_step": 10,
            "enabled": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        # Round-trip via the same legacy (unscoped) path
        get_resp = self.client.get("/api/dumping/configs")
        self.assertIn("PILOT-SKU-1", get_resp.json()["configs"])

    def test_missing_legacy_session_returns_401_same_as_before(self):
        os.remove("states/session.json")
        resp = self.client.get("/api/dumping/configs")
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
