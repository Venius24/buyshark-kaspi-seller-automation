"""
Closes the stores_v1.py gap in HANDOFF 3.1: /api/v1/stores/connect-full-store,
/select, /verify and /sync-cookies must never read/write the shared global
state.json/session.json for MC credentials or cookies, and must never fall
back to another store's (or the legacy global) session when a store's own
credentials/cookies are missing or invalid.
"""
import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.db.models import User, Store
from backend.routers.auth_v1 import get_current_user as real_get_current_user
from backend.db.database import get_db as real_get_db
from backend.security.crypto import encrypt_data, decrypt_data


def make_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    result.first.return_value = value
    return result


class TestStoresV1NoFallback(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(Store(id="store-existing-1", merchant_id="m_existing", name="ExistingShop"))
        session.add(Store(id="store-other-1", merchant_id="m_other", name="OtherShop"))
        session.commit()
        session.close()

        self.client = TestClient(app)
        app.dependency_overrides[real_get_current_user] = lambda: User(
            id="user-1", email="a@b.com", password_hash="x", is_active=True
        )

    def tearDown(self):
        app.dependency_overrides.pop(real_get_current_user, None)
        app.dependency_overrides.pop(real_get_db, None)
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _read_store(self, store_id):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        try:
            return session.get(Store, store_id)
        finally:
            session.close()

    def _override_db(self, fake_db):
        async def fake_get_db_gen():
            yield fake_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen

    @patch("backend.routers.stores_v1.worker")
    @patch("backend.routers.stores_v1.KaspiAPI")
    def test_connect_full_store_persists_creds_to_resolved_store_only(self, mock_kaspi, mock_worker):
        """connect-full-store must save MC creds onto the resolved Store row
        (via store_id), never into the shared global session.json, and must
        never touch any OTHER store's row."""
        mock_kaspi.verify_token.return_value = True
        mock_kaspi.get_merchant_id.return_value = "m_existing"
        # Де-глобализация Playwright: LOGIN отдаёт куки напрямую (3-tuple),
        # не через общий state.json — см. playwright_worker.py.
        mock_worker.send_cmd.return_value = (True, "Success", {"mc-session": "fresh-sess", "mc-sid": "fresh-sid"})

        existing_store_obj = Store(
            id="store-existing-1", merchant_id="m_existing", name="ExistingShop",
            encrypted_api_key=None, encrypted_cookies=None, status="PAUSED",
            created_at=datetime.utcnow(),
        )
        fake_db = AsyncMock()
        fake_db.execute.side_effect = [
            make_result(None),                  # check_quota_guard: no Subscription row
            make_result(0),                     # owned-store count
            make_result(existing_store_obj),    # existing-store lookup
            make_result(object()),              # UserStore link already exists
        ]
        self._override_db(fake_db)

        resp = self.client.post(
            "/api/v1/stores/connect-full-store",
            json={
                "name": "ExistingShop",
                "api_token": "some-real-token",
                "email": "shop@kaspi.kz",
                "password": "hunter2",
            },
        )

        self.assertEqual(resp.status_code, 200, resp.text)

        db_store = self._read_store("store-existing-1")
        self.assertEqual(decrypt_data(db_store.encrypted_mc_email), "shop@kaspi.kz")
        self.assertEqual(decrypt_data(db_store.encrypted_mc_password), "hunter2")

        other_store = self._read_store("store-other-1")
        self.assertIsNone(other_store.encrypted_mc_email)
        self.assertIsNone(other_store.encrypted_mc_password)

    @patch("backend.routers.stores_v1._refresh_mc_session")
    def test_sync_cookies_no_fallback_requires_own_creds(self, mock_refresh):
        """When this store's own refresh fails, sync-cookies must reject with
        a re-login message -- never fall back to system/global cookies."""
        mock_refresh.return_value = False

        store_row = Store(
            id="store-existing-1", merchant_id="m_existing", name="ExistingShop",
            created_at=datetime.utcnow(),
        )
        fake_db = AsyncMock()
        fake_db.execute.side_effect = [make_result((store_row, "OWNER"))]
        self._override_db(fake_db)

        resp = self.client.post("/api/v1/stores/store-existing-1/sync-cookies")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Войдите", resp.json()["detail"])
        mock_refresh.assert_called_once_with(store_id="store-existing-1")

    @patch("backend.routers.stores_v1.requests")
    @patch("backend.routers.stores_v1._refresh_mc_session")
    def test_verify_no_fallback_marks_expired_when_own_refresh_fails(self, mock_refresh, mock_requests):
        """When this store's cookies are invalid and its own refresh fails,
        /verify must mark it COOKIE_EXPIRED -- no global-cookie fallback."""
        mock_requests.get.return_value = MagicMock(status_code=401)
        mock_refresh.return_value = False

        store_row = Store(
            id="store-existing-1", merchant_id="m_existing", name="ExistingShop",
            encrypted_cookies=encrypt_data("mc-session=stale; mc-sid=stale"),
            created_at=datetime.utcnow(),
        )
        fake_db = AsyncMock()
        fake_db.execute.side_effect = [make_result((store_row, "OWNER"))]
        self._override_db(fake_db)

        resp = self.client.post("/api/v1/stores/store-existing-1/verify")

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "COOKIE_EXPIRED")
        mock_refresh.assert_called_once_with(store_id="store-existing-1")

    def test_select_endpoint_removed(self):
        """Слайс 6: /select удалён целиком — раньше он писал расшифрованный
        API-токен выбранного магазина в общий states/session.json, и с тех
        пор как фронтенд выбирает магазин через localStorage + X-Store-Id
        (storeContext.ts), этот эндпоинт больше не вызывается и был вреден
        (тихо переключал общую легаси-сессию на последний выбранный магазин)."""
        resp = self.client.post("/api/v1/stores/store-existing-1/select")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
