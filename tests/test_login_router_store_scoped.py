"""
End-to-end check for closing HANDOFF 3.1, part (a): /api/login resolves the
caller's store via the existing dual-auth get_store_context dependency (no
new frontend state needed) and persists creds + fresh cookies EXCLUSIVELY
into that Store's DB row — not into the shared state.json/session.json.
"""
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


class TestLoginRouterStoreScoped(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)
        # Simulates the Playwright worker's own output mechanism (single
        # shared browser session, Phase 5 limitation) — /api/login reads
        # this once after a successful login to copy fresh cookies into DB.
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "fresh-sess"},
                {"name": "mc-sid", "value": "fresh-sid"},
            ]}, f)

        # Real Store row in the (tmp-dir-scoped, fresh) sync DB so
        # _save_mc_credentials/_save_mc_cookies_for_store have something to
        # write into and we can assert on real persisted state afterwards.
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(Store(id="store-1", merchant_id="m_new", name="Shop"))
        session.commit()
        session.close()

        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _read_store_from_db(self):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        try:
            return session.get(Store, "store-1")
        finally:
            session.close()

    @patch("backend.routers.auth.worker")
    @patch("backend.security.store_context.decrypt_data")
    def test_login_persists_creds_and_cookies_to_resolved_store_only(self, mock_decrypt, mock_worker):
        mock_decrypt.return_value = "rest_token"
        mock_worker.send_cmd.return_value = (True, "Success")

        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-1", merchant_id="m_new", name="Shop", encrypted_api_key="cipher")

        fake_db = AsyncMock()
        fake_db.execute.side_effect = [make_scalar_result(user), make_scalar_result(store)]

        async def fake_get_db_gen():
            yield fake_db

        from backend.db.database import get_db as real_get_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen
        try:
            resp = self.client.post(
                "/api/login",
                json={"email": "shop@kaspi.kz", "password": "hunter2"},
                headers={"Authorization": f"Bearer {access_tok}"},
            )
        finally:
            app.dependency_overrides.pop(real_get_db, None)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        # worker.send_cmd got the real submitted credentials
        mock_worker.send_cmd.assert_called_once()
        call_args = mock_worker.send_cmd.call_args[0]
        self.assertEqual(call_args[1], {"email": "shop@kaspi.kz", "password": "hunter2"})

        # Persisted state lives on the resolved Store row, not the shared files.
        db_store = self._read_store_from_db()
        self.assertIsNotNone(db_store.encrypted_cookies)
        self.assertIsNotNone(db_store.encrypted_mc_email)
        self.assertIsNotNone(db_store.encrypted_mc_password)
        self.assertEqual(db_store.status, "ACTIVE")

        from backend.security.crypto import decrypt_data
        self.assertEqual(decrypt_data(db_store.encrypted_mc_email), "shop@kaspi.kz")
        self.assertEqual(decrypt_data(db_store.encrypted_mc_password), "hunter2")
        cookie_str = decrypt_data(db_store.encrypted_cookies)
        self.assertIn("mc-session=fresh-sess", cookie_str)
        self.assertIn("mc-sid=fresh-sid", cookie_str)

    @patch("backend.routers.auth.worker")
    def test_login_with_no_jwt_returns_401(self, mock_worker):
        """Слайс 7: legacy-фоллбек на общую сессию убран целиком — без
        Authorization заголовка /api/login отдаёт 401, Store не трогается."""
        mock_worker.send_cmd.return_value = (True, "Success")

        resp = self.client.post(
            "/api/login",
            json={"email": "shop@kaspi.kz", "password": "hunter2"},
        )
        self.assertEqual(resp.status_code, 401)

        db_store = self._read_store_from_db()
        self.assertIsNone(db_store.encrypted_cookies)
        self.assertIsNone(db_store.encrypted_mc_email)


if __name__ == "__main__":
    unittest.main()
