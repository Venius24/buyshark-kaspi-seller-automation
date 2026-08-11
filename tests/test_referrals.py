import os
import sys
import shutil
import tempfile
import unittest
import datetime as _dt
from unittest.mock import patch

sys.path.append(os.getcwd())


class ReferralAccrualTest(unittest.TestCase):
    """Реферальные начисления (Слайс 7): 50% от реального пополнения, целое
    число тиын, одно начисление на транзакцию, окно/счётчик периодов,
    потолок — не больше commission_percent% от реально списанного в окне."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "referrals_test.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._session_patch.start()

        from backend.db.models import Store, User, ReferralProfileDB, StoreReferralDB
        now = _dt.datetime.utcnow()
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(User(id="ref-user", email="ref@example.com", password_hash="x"))
        session.add(ReferralProfileDB(user_id="ref-user", code="ABC12345", commission_percent=50, commission_months=3, is_active=True))
        session.add(StoreReferralDB(
            store_id="store-a", referrer_user_id="ref-user", source="manual",
            window_start=now - _dt.timedelta(days=1), window_end=now + _dt.timedelta(days=89),
        ))
        session.commit()
        session.close()

        from backend.billing import referrals, ledger
        self.referrals = referrals
        self.ledger = ledger

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _charge(self, amount_minor, key):
        return self.ledger.debit("store-a", amount_minor, "charge_subscription", idempotency_key=key)

    def test_accrual_is_exact_50_percent(self):
        self._charge(640_000, "c1")
        result = self.referrals.accrue_on_topup("store-a", "txn-1", 640_000)
        self.assertIsNotNone(result)
        self.assertEqual(result["amount_minor"], 320_000)

    def test_same_transaction_not_duplicated(self):
        self._charge(640_000, "c1")
        first = self.referrals.accrue_on_topup("store-a", "txn-1", 640_000)
        second = self.referrals.accrue_on_topup("store-a", "txn-1", 640_000)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

        from backend.db.models import ReferralAccrualDB
        session = self.state_helpers._get_sync_db_session()
        count = session.query(ReferralAccrualDB).filter_by(source_transaction_id="txn-1").count()
        session.close()
        self.assertEqual(count, 1)

    def test_capped_by_amount_actually_charged_in_window(self):
        # Списано за тариф всего 640_000 в окне -> потолок начисления 50% = 320_000.
        # Пополнение на 1_000_000 не должно выплатить рефереру больше потолка.
        self._charge(640_000, "c1")
        result = self.referrals.accrue_on_topup("store-a", "txn-big", 1_000_000)
        self.assertIsNotNone(result)
        self.assertEqual(result["amount_minor"], 320_000)

    def test_no_charged_amount_means_no_accrual(self):
        result = self.referrals.accrue_on_topup("store-a", "txn-1", 500_000)
        self.assertIsNone(result)

    def test_window_end_blocks_accrual(self):
        from backend.db.models import StoreReferralDB
        session = self.state_helpers._get_sync_db_session()
        ref = session.get(StoreReferralDB, "store-a")
        ref.window_end = _dt.datetime.utcnow() - _dt.timedelta(days=1)
        session.commit()
        session.close()

        self._charge(640_000, "c1")
        result = self.referrals.accrue_on_topup("store-a", "txn-1", 640_000)
        self.assertIsNone(result)

    def test_fourth_period_does_not_accrue(self):
        from backend.db.models import ReferralAccrualDB
        session = self.state_helpers._get_sync_db_session()
        for i in range(3):
            session.add(ReferralAccrualDB(
                id=f"acc-{i}", referrer_user_id="ref-user", store_id="store-a",
                source_transaction_id=f"prior-{i}", amount_minor=100_000, percent=50,
                period_start=_dt.datetime(2026, i + 1, 1), period_end=_dt.datetime(2026, i + 1, 1),
                status="accrued",
            ))
        session.commit()
        session.close()

        self._charge(640_000, "c1")
        result = self.referrals.accrue_on_topup("store-a", "txn-new", 640_000)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
