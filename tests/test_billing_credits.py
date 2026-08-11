import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class BillingCreditsTest(unittest.TestCase):
    """CAS-расход дневных кредитов ответов ИИ (Слайс 3): ленивое создание
    строки дня, отказ при превышении лимита, множитель промпта, новый день
    без job'а сброса, release() не уходит ниже нуля."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "credits_test.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._session_patch.start()

        from backend.db.models import Store, StoreTariffDB
        import datetime as _dt
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(StoreTariffDB(
            store_id="store-a", plan_code="START", credit_addon_code="NONE",
            billing_status="ACTIVE", billing_anchor_day=1,
            period_start=_dt.datetime.utcnow(), period_end=_dt.datetime.utcnow(),
            next_charge_at=_dt.datetime.utcnow(), activated_at=_dt.datetime.utcnow(),
        ))
        session.commit()
        session.close()

        from backend.billing import credits
        self.credits = credits

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_lazy_row_creation_on_first_consume(self):
        result = self.credits.try_consume("store-a", 1000)
        self.assertTrue(result.ok)
        self.assertEqual(result.used_milli, 1000)
        self.assertEqual(result.limit_milli, 50_000)

    def test_cas_rejects_over_limit(self):
        self.credits.try_consume("store-a", 49_000)
        result = self.credits.try_consume("store-a", 2000)
        self.assertFalse(result.ok)
        self.assertEqual(result.used_milli, 49_000)

    def test_exact_boundary_allowed(self):
        self.credits.try_consume("store-a", 49_000)
        result = self.credits.try_consume("store-a", 1000)
        self.assertTrue(result.ok)
        self.assertEqual(result.used_milli, 50_000)

    def test_new_day_resets_without_job(self):
        import datetime as _dt
        yesterday = _dt.datetime.utcnow() - _dt.timedelta(days=1)
        self.credits.try_consume("store-a", 49_000, now=yesterday)
        result = self.credits.try_consume("store-a", 49_000)
        self.assertTrue(result.ok)

    def test_release_does_not_go_below_zero(self):
        self.credits.try_consume("store-a", 1000)
        self.credits.release("store-a", 5000)
        usage = self.credits.today_usage("store-a")
        self.assertEqual(usage.used_milli, 0)

    def test_dev_plan_practically_unlimited(self):
        from backend.db.models import StoreTariffDB
        session = self.state_helpers._get_sync_db_session()
        tariff = session.get(StoreTariffDB, "store-a")
        tariff.plan_code = "DEV"
        session.commit()
        session.close()
        result = self.credits.try_consume("store-a", 500_000)
        self.assertTrue(result.ok)

    def test_today_usage_read_only_does_not_create_row(self):
        usage = self.credits.today_usage("store-a")
        self.assertEqual(usage.used_milli, 0)
        self.assertEqual(usage.limit_milli, 50_000)

        from backend.db.models import StoreDailyCreditDB
        session = self.state_helpers._get_sync_db_session()
        count = session.query(StoreDailyCreditDB).count()
        session.close()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
