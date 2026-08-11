import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class BillingLedgerTest(unittest.TestCase):
    """Append-only леджер (Слайс 4): идемпотентность через UNIQUE(idempotency_key),
    баланс уходит в минус без ошибки, вставка+движение баланса атомарны."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "ledger_test.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._session_patch.start()

        from backend.db.models import Store
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.commit()
        session.close()

        from backend.billing import ledger
        self.ledger = ledger

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_credit_creates_balance_row_lazily(self):
        result = self.ledger.credit("store-a", 100_000, "topup_manual", idempotency_key="k1")
        self.assertTrue(result.applied)
        self.assertEqual(result.balance_minor, 100_000)

    def test_same_idempotency_key_applies_once(self):
        r1 = self.ledger.credit("store-a", 50_000, "topup_manual", idempotency_key="dup-key")
        r2 = self.ledger.credit("store-a", 50_000, "topup_manual", idempotency_key="dup-key")
        self.assertTrue(r1.applied)
        self.assertFalse(r2.applied)
        self.assertEqual(r2.reason, "already_applied")
        self.assertEqual(self.ledger.balance("store-a"), 50_000)

    def test_debit_can_go_negative(self):
        result = self.ledger.debit("store-a", 30_000, "charge_subscription", idempotency_key="d1")
        self.assertTrue(result.applied)
        self.assertEqual(result.balance_minor, -30_000)

    def test_transactions_listed_newest_first(self):
        self.ledger.credit("store-a", 10_000, "topup_manual", idempotency_key="a")
        self.ledger.debit("store-a", 5_000, "charge_subscription", idempotency_key="b")
        txns = self.ledger.list_transactions("store-a")
        self.assertEqual(len(txns), 2)
        self.assertEqual(txns[0]["kind"], "charge_subscription")
        self.assertEqual(txns[1]["kind"], "topup_manual")


if __name__ == "__main__":
    unittest.main()
