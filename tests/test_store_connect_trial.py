"""7-дневный триал при подключении нового магазина (POST
/api/v1/stores/connect-full-store с plan_code): выбранный пользователем
платный тариф ставится сразу с коротким первым периодом (7 дней вместо
обычных 31) -- та же механика trial_days, что и в admin_billing.py, просто
триггер теперь ещё и на стороне пользователя. Триал даётся ТОЛЬКО на
абсолютно новом магазине; при реконнекте уже существующего (у него уже
есть строка store_tariffs) plan_code из этого эндпоинта не принимается
вообще -- триал не может быть выдан повторно."""
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
from backend.db.models import User, Store, StoreTariffDB, Subscription
from backend.routers.auth_v1 import get_current_user
from backend.db.database import get_db


class StoreConnectTrialTest(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(User(id="user-1", email="a@b.com", password_hash="x", is_active=True))
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
        app.dependency_overrides[get_current_user] = lambda: User(
            id="user-1", email="a@b.com", password_hash="x", is_active=True
        )
        app.dependency_overrides[get_db] = _fake_get_db

    def tearDown(self):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _tariff_for(self, store_id):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        tariff = session.get(StoreTariffDB, store_id)
        session.expunge(tariff)
        session.close()
        return tariff

    @patch("backend.routers.stores_v1.KaspiAPI")
    def test_new_store_with_plan_code_gets_seven_day_trial(self, mock_kaspi):
        mock_kaspi.verify_token.return_value = True
        mock_kaspi.get_merchant_id.return_value = "m_new_trial"

        before = _dt.datetime.utcnow()
        resp = self.client.post(
            "/api/v1/stores/connect-full-store",
            json={"name": "NewTrialShop", "api_token": "tok-1", "plan_code": "HUNTER"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        store_id = resp.json()["id"]

        tariff = self._tariff_for(store_id)
        self.assertEqual(tariff.plan_code, "HUNTER")
        delta = tariff.next_charge_at - before
        self.assertGreater(delta, _dt.timedelta(days=6, hours=23))
        self.assertLess(delta, _dt.timedelta(days=7, hours=1))

    @patch("backend.routers.stores_v1.KaspiAPI")
    def test_new_store_without_plan_code_stays_dev(self, mock_kaspi):
        mock_kaspi.verify_token.return_value = True
        mock_kaspi.get_merchant_id.return_value = "m_new_dev"

        resp = self.client.post(
            "/api/v1/stores/connect-full-store",
            json={"name": "NewDevShop", "api_token": "tok-2"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        store_id = resp.json()["id"]

        tariff = self._tariff_for(store_id)
        self.assertEqual(tariff.plan_code, "DEV")
        self.assertGreater(tariff.next_charge_at - _dt.datetime.utcnow(), _dt.timedelta(days=365))

    @patch("backend.routers.stores_v1.KaspiAPI")
    def test_invalid_plan_code_rejected(self, mock_kaspi):
        mock_kaspi.verify_token.return_value = True
        mock_kaspi.get_merchant_id.return_value = "m_bad_plan"

        resp = self.client.post(
            "/api/v1/stores/connect-full-store",
            json={"name": "BadPlanShop", "api_token": "tok-3", "plan_code": "DEV"},
        )
        self.assertEqual(resp.status_code, 400)

        resp2 = self.client.post(
            "/api/v1/stores/connect-full-store",
            json={"name": "BadPlanShop2", "api_token": "tok-4", "plan_code": "NOT_A_PLAN"},
        )
        self.assertEqual(resp2.status_code, 400)

    @patch("backend.routers.stores_v1.KaspiAPI")
    def test_reconnect_existing_store_ignores_plan_code(self, mock_kaspi):
        """У существующего магазина уже есть store_tariffs -- повторный
        коннект с plan_code не должен пересоздавать/менять её на триал."""
        mock_kaspi.verify_token.return_value = True
        mock_kaspi.get_merchant_id.return_value = "m_existing_reco"

        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(Subscription(
            user_id="user-1", plan_type="FREE", max_stores=2, max_skus_in_auto_price=1000,
            valid_until=_dt.datetime.utcnow() + _dt.timedelta(days=365),
        ))
        session.commit()
        session.close()

        resp = self.client.post(
            "/api/v1/stores/connect-full-store",
            json={"name": "ExistingShop", "api_token": "tok-5"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        store_id = resp.json()["id"]
        tariff_before = self._tariff_for(store_id)
        self.assertEqual(tariff_before.plan_code, "DEV")

        resp2 = self.client.post(
            "/api/v1/stores/connect-full-store",
            json={"name": "ExistingShop", "api_token": "tok-5", "plan_code": "HUNTER"},
        )
        self.assertEqual(resp2.status_code, 200, resp2.text)
        tariff_after = self._tariff_for(store_id)
        self.assertEqual(tariff_after.plan_code, "DEV")


if __name__ == "__main__":
    unittest.main()
