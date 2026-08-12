import os
import sys
import shutil
import tempfile
import unittest
import datetime as _dt
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.db.models import Store, User, ReferralProfileDB, StoreReferralDB
from backend.routers.auth_v1 import get_current_user
from backend.db.database import get_db


# Каждый маршрут /api/admin/* — параметризованный список ловит новый
# эндпоинт, добавленный без Depends(require_superadmin) на самом router'е
# (Слайс 4/7: зависимость висит на APIRouter, но регрессия должна поймать
# случай, если кто-то однажды соберёт отдельный router без неё).
ADMIN_ROUTES = [
    ("GET", "/api/admin/stores"),
    ("GET", "/api/admin/stores/store-a"),
    ("POST", "/api/admin/stores/store-a/tariff", {"plan_code": "START"}),
    ("POST", "/api/admin/stores/store-a/topup", {"amount_minor": 100, "idempotency_key": "k1"}),
    ("POST", "/api/admin/stores/store-a/adjust", {"amount_minor": 100, "idempotency_key": "k2"}),
    ("POST", "/api/admin/stores/store-a/referrer", {"referrer_user_id": "ref-user"}),
    ("DELETE", "/api/admin/stores/store-a/referrer"),
    ("POST", "/api/admin/stores/store-a/reevaluate"),
    ("POST", "/api/admin/stores/store-a/charge-now"),
    ("GET", "/api/admin/transactions?store_id=store-a"),
    ("GET", "/api/admin/referrers"),
    ("POST", "/api/admin/referrers", {"user_id": "ref-user"}),
    ("GET", "/api/admin/users/lookup?email=x@example.com"),
    ("GET", "/api/admin/referrers/ref-user/accruals"),
    ("POST", "/api/admin/accruals/acc-1/mark-paid"),
]


class PlatformRolesTest(unittest.TestCase):
    """require_superadmin/require_referrer (Слайс 4/7): не-суперадмин
    получает 403 на КАЖДОМ /api/admin/* маршруте; реферер видит только свои
    магазины, подмена id в запросе ничего не меняет (referrer_user_id всегда
    берётся из require_referrer, никогда из query/body)."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend.state_helpers import _get_sync_db_session
        now = _dt.datetime.utcnow()
        session = _get_sync_db_session()
        session.add(User(id="plain-user", email="plain@example.com", password_hash="x", is_active=True, is_superadmin=False))
        session.add(User(id="admin-user", email="admin@example.com", password_hash="x", is_active=True, is_superadmin=True))
        session.add(User(id="ref-user-a", email="refa@example.com", password_hash="x", is_active=True))
        session.add(User(id="ref-user-b", email="refb@example.com", password_hash="x", is_active=True))
        session.add(ReferralProfileDB(user_id="ref-user-a", code="CODEAAAA", commission_percent=50, commission_months=3, is_active=True))
        session.add(ReferralProfileDB(user_id="ref-user-b", code="CODEBBBB", commission_percent=50, commission_months=3, is_active=True))

        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(Store(id="store-b", merchant_id="m_b", name="Магазин Б"))
        session.add(StoreReferralDB(
            store_id="store-a", referrer_user_id="ref-user-a", source="manual",
            window_start=now - _dt.timedelta(days=1), window_end=now + _dt.timedelta(days=89),
        ))
        session.add(StoreReferralDB(
            store_id="store-b", referrer_user_id="ref-user-b", source="manual",
            window_start=now - _dt.timedelta(days=1), window_end=now + _dt.timedelta(days=89),
        ))
        session.commit()
        session.close()

        import glob
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        db_files = glob.glob(os.path.join(self._tmp_dir, "*.db"))
        assert db_files, "sync session did not create a sqlite file"
        db_path = db_files[0].replace(os.sep, "/")
        self._async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self._AsyncSessionLocal = async_sessionmaker(bind=self._async_engine, expire_on_commit=False)

        async def _fake_get_db():
            async with self._AsyncSessionLocal() as s:
                yield s

        self.client = TestClient(app)
        self._fake_user = None
        app.dependency_overrides[get_current_user] = lambda: self._fake_user
        app.dependency_overrides[get_db] = _fake_get_db

    def tearDown(self):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _as(self, user_id: str):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        user = session.get(User, user_id)
        session.close()
        self._fake_user = user

    def _call(self, method, path, body=None):
        if method == "GET":
            return self.client.get(path)
        if method == "POST":
            return self.client.post(path, json=body or {})
        if method == "DELETE":
            return self.client.delete(path)
        raise ValueError(method)

    def test_every_admin_route_403s_for_plain_user(self):
        self._as("plain-user")
        for route in ADMIN_ROUTES:
            method, path = route[0], route[1]
            body = route[2] if len(route) > 2 else None
            with self.subTest(route=f"{method} {path}"):
                resp = self._call(method, path, body)
                self.assertEqual(resp.status_code, 403, f"{method} {path} -> {resp.status_code}: {resp.text}")

    def test_every_admin_route_401s_without_auth(self):
        """Без override — реальный get_current_user требует валидный JWT и
        сам бросает 401 раньше, чем дело доходит до require_superadmin."""
        app.dependency_overrides.pop(get_current_user, None)
        try:
            for route in ADMIN_ROUTES:
                method, path = route[0], route[1]
                body = route[2] if len(route) > 2 else None
                with self.subTest(route=f"{method} {path}"):
                    resp = self._call(method, path, body)
                    self.assertEqual(resp.status_code, 401, f"{method} {path} -> {resp.status_code}: {resp.text}")
        finally:
            app.dependency_overrides[get_current_user] = lambda: self._fake_user

    def test_superadmin_can_reach_admin_routes(self):
        self._as("admin-user")
        resp = self.client.get("/api/admin/stores")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_plain_user_gets_403_on_referral_routes(self):
        self._as("plain-user")
        for path in ("/api/referral/me", "/api/referral/stores", "/api/referral/accruals"):
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 403)

    def test_referrer_sees_only_own_stores(self):
        self._as("ref-user-a")
        resp = self.client.get("/api/referral/stores")
        self.assertEqual(resp.status_code, 200, resp.text)
        store_ids = [row["store_id"] for row in resp.json()]
        self.assertEqual(store_ids, ["store-a"])
        self.assertNotIn("store-b", store_ids)

    def test_referrer_me_scope_is_from_token_not_request(self):
        """Нет параметра, которым можно подменить referrer_user_id — /me
        всегда читает профиль из require_referrer (JWT), не из query."""
        self._as("ref-user-a")
        resp = self.client.get("/api/referral/me?referrer_user_id=ref-user-b")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["code"], "CODEAAAA")

        self._as("ref-user-b")
        resp2 = self.client.get("/api/referral/me")
        self.assertEqual(resp2.json()["code"], "CODEBBBB")


if __name__ == "__main__":
    unittest.main()
