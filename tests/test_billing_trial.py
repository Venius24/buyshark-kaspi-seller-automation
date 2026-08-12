import os
import sys
import shutil
import tempfile
import unittest
import datetime as _dt

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.db.models import Store, User, StoreTariffDB, StoreBalanceDB
from backend.routers.auth_v1 import get_current_user
from backend.db.database import get_db


class BillingTrialTest(unittest.TestCase):
    """trial_days (POST /api/admin/stores/{id}/tariff): переход с DEV на
    платный план с сокращённым первым периодом. Никакой отдельной триальной
    логики в движке нет намеренно — трюк только в том, что первый
    period_end/next_charge_at короче обычных 31 дня, дальше магазин идёт по
    тем же рельсам charge_period()/evaluate_block(), что и обычная неоплата."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend.state_helpers import _get_sync_db_session
        now = _dt.datetime.utcnow()
        session = _get_sync_db_session()
        session.add(User(id="admin-user", email="admin@example.com", password_hash="x", is_active=True, is_superadmin=True))
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(StoreTariffDB(
            store_id="store-a", plan_code="DEV",
            period_start=now, period_end=now, next_charge_at=now, activated_at=now,
        ))
        session.add(StoreBalanceDB(store_id="store-a", balance_minor=0))
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

        from backend.state_helpers import _get_sync_db_session as _s
        session2 = _s()
        self._fake_user = session2.get(User, "admin-user")
        session2.close()

    def tearDown(self):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _tariff(self):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        tariff = session.get(StoreTariffDB, "store-a")
        session.expunge(tariff)
        session.close()
        return tariff

    def test_trial_days_shortens_first_period(self):
        before = _dt.datetime.utcnow()
        resp = self.client.post("/api/admin/stores/store-a/tariff", json={"plan_code": "HUNTER", "trial_days": 7})
        self.assertEqual(resp.status_code, 200, resp.text)

        tariff = self._tariff()
        self.assertEqual(tariff.plan_code, "HUNTER")
        delta = tariff.next_charge_at - before
        self.assertGreater(delta, _dt.timedelta(days=6, hours=23))
        self.assertLess(delta, _dt.timedelta(days=7, hours=1))
        self.assertEqual(tariff.period_start, tariff.activated_at)

    def test_no_trial_days_defaults_to_31(self):
        before = _dt.datetime.utcnow()
        resp = self.client.post("/api/admin/stores/store-a/tariff", json={"plan_code": "HUNTER"})
        self.assertEqual(resp.status_code, 200, resp.text)

        tariff = self._tariff()
        delta = tariff.next_charge_at - before
        self.assertGreater(delta, _dt.timedelta(days=30, hours=23))
        self.assertLess(delta, _dt.timedelta(days=31, hours=1))

    def test_trial_days_out_of_range_rejected(self):
        resp = self.client.post("/api/admin/stores/store-a/tariff", json={"plan_code": "HUNTER", "trial_days": 0})
        self.assertEqual(resp.status_code, 400)
        resp2 = self.client.post("/api/admin/stores/store-a/tariff", json={"plan_code": "HUNTER", "trial_days": 91})
        self.assertEqual(resp2.status_code, 400)

    def test_trial_expiry_charges_full_price_and_can_block(self):
        """Симулируем истечение триала: тик воркера charge_period() списывает
        полную месячную цену тарифа, а не какую-то уменьшенную триальную —
        отдельной ветки для этого в движке нет и не должно быть."""
        from backend.billing import engine as billing_engine, catalog

        resp = self.client.post("/api/admin/stores/store-a/tariff", json={"plan_code": "HUNTER", "trial_days": 7})
        self.assertEqual(resp.status_code, 200, resp.text)

        future = _dt.datetime.utcnow() + _dt.timedelta(days=8)
        charged = billing_engine.charge_period("store-a", now=future)
        self.assertEqual(charged, 1)

        from backend.billing import ledger
        balance = ledger.balance("store-a")
        expected_price = catalog.plan_price_minor("HUNTER", None)
        self.assertEqual(balance, -expected_price)

        status = billing_engine.evaluate_block("store-a", now=future)
        self.assertEqual(status, "BLOCKED_BALANCE")


if __name__ == "__main__":
    unittest.main()
