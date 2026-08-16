"""
Регрессия (2026-07-30): GET /api/mc-status?verify=false раньше возвращал
valid=True как только куки просто расшифровывались, игнорируя Store.status.
Это давало рассинхрон с StoreHub (честно читает Store.status): куки лежат
в БД и корректно расшифровываются, но давно помечены COOKIE_EXPIRED
health_check_worker'ом/verify — а бейдж в Настройках всё равно светился
зелёным. valid при verify=False обязан отражать тот же Store.status.
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, AsyncMock

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


class TestMcStatusReflectsStoreStatus(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend.state_helpers import _get_sync_db_session
        from backend.security.crypto import encrypt_data
        session = _get_sync_db_session()
        session.add(Store(
            id="store-1", merchant_id="m_1", name="Shop",
            encrypted_cookies=encrypt_data("mc-session=s; mc-sid=sid"),
            status="COOKIE_EXPIRED",
        ))
        session.commit()
        session.close()

        self.client = TestClient(app)
        self.access_tok = create_access_token({"sub": "user-1"})
        self.user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _set_store_status(self, status: str):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        store = session.get(Store, "store-1")
        store.status = status
        session.commit()
        session.close()

    def _call_mc_status(self, store: Store):
        fake_db = AsyncMock()
        fake_db.execute.side_effect = [make_scalar_result(self.user), make_scalar_result(store)]

        async def fake_get_db_gen():
            yield fake_db

        from backend.db.database import get_db as real_get_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen
        # get_store_context держит свою сессию, не Depends(get_db) — см. store_context_stub.
        install_store_context_db(self, fake_db)
        try:
            return self.client.get(
                "/api/mc-status?verify=false",
                headers={"Authorization": f"Bearer {self.access_tok}"},
            )
        finally:
            app.dependency_overrides.pop(real_get_db, None)

    def test_cookies_present_but_status_expired_reports_invalid(self):
        from backend.security.crypto import encrypt_data
        store = Store(id="store-1", merchant_id="m_1", name="Shop", encrypted_api_key=encrypt_data("rest_token"))
        resp = self._call_mc_status(store)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["has_session"])
        self.assertFalse(data["valid"])

    def test_cookies_present_and_status_active_reports_valid(self):
        from backend.security.crypto import encrypt_data
        self._set_store_status("ACTIVE")
        store = Store(id="store-1", merchant_id="m_1", name="Shop", encrypted_api_key=encrypt_data("rest_token"))
        resp = self._call_mc_status(store)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["has_session"])
        self.assertTrue(data["valid"])


if __name__ == "__main__":
    unittest.main()
