"""
Closes the stores_v1.py gap in HANDOFF 3.1: /api/v1/stores/connect-full-store,
/select, /verify and /sync-cookies must never read/write the shared global
state.json/session.json for MC credentials or cookies, and must never fall
back to another store's (or the legacy global) session when a store's own
credentials/cookies are missing or invalid.

Пул Postgres (HANDOFF 3.52/§аудит): эти три эндпоинта больше не держат
Depends(get_db) через сетевые фазы — они открывают короткие scoped-сессии
сами. Поэтому тесты ходят в реальную SQLite (как test_store_connect_trial),
а не подсовывают AsyncMock-последовательности execute().
"""
import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.db.models import User, Store, UserStore
from backend.routers.auth_v1 import get_current_user as real_get_current_user
from backend.security.crypto import encrypt_data, decrypt_data


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

        # Эндпоинты после рефакторинга пула открывают сессии сами, через
        # AsyncSessionLocal (не через Depends(get_db)). Глобальный движок
        # держит абсолютный путь к dev-sqlite репозитория — подменяем
        # фабрику в модуле роутера на движок поверх временного файла.
        import glob
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        import backend.routers.stores_v1 as stores_v1_mod
        db_files = glob.glob(os.path.join(self._tmp_dir, "*.db"))
        assert db_files, "sync session did not create a sqlite file"
        db_path = db_files[0].replace(os.sep, "/")
        self._async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self._AsyncSessionLocal = async_sessionmaker(bind=self._async_engine, expire_on_commit=False)
        self._asl_patch = patch.object(stores_v1_mod, "AsyncSessionLocal", self._AsyncSessionLocal)
        self._asl_patch.start()

        self.client = TestClient(app)
        app.dependency_overrides[real_get_current_user] = lambda: User(
            id="user-1", email="a@b.com", password_hash="x", is_active=True
        )

    def tearDown(self):
        app.dependency_overrides.pop(real_get_current_user, None)
        self._asl_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _seed_owner_link(self, store_id="store-existing-1"):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(UserStore(user_id="user-1", store_id=store_id, role="OWNER"))
        session.commit()
        session.close()

    def _read_store(self, store_id):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        try:
            return session.get(Store, store_id)
        finally:
            session.close()

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
        self._seed_owner_link()
        mock_refresh.return_value = False

        resp = self.client.post("/api/v1/stores/store-existing-1/sync-cookies")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Войдите", resp.json()["detail"])
        mock_refresh.assert_called_once_with(store_id="store-existing-1")

    @patch("backend.routers.stores_v1.worker")
    @patch("backend.routers.stores_v1._load_mc_cookies")
    @patch("backend.routers.stores_v1._refresh_mc_session")
    def test_verify_no_fallback_marks_expired_when_own_refresh_fails(self, mock_refresh, mock_load_cookies, mock_worker):
        """When this store's cookies are invalid and its own refresh fails,
        /verify must mark it COOKIE_EXPIRED -- no global-cookie fallback."""
        self._seed_owner_link()
        mock_load_cookies.return_value = {"mc-session": "stale", "mc-sid": "stale"}
        mock_worker.send_cmd.return_value = (True, False, {})  # success, is_valid=False
        mock_refresh.return_value = False

        resp = self.client.post("/api/v1/stores/store-existing-1/verify")

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "COOKIE_EXPIRED")
        mock_refresh.assert_called_once_with(store_id="store-existing-1")
        # Вердикт реально доехал до БД короткой scoped-сессией.
        self.assertEqual(self._read_store("store-existing-1").status, "COOKIE_EXPIRED")

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
