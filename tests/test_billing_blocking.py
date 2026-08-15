import os
import sys
import shutil
import tempfile
import unittest
import datetime as _dt
from unittest.mock import patch

sys.path.append(os.getcwd())


class BillingBlockingTest(unittest.TestCase):
    """evaluate_block() (Слайс 6): превышение лимита товаров блокирует и НЕ
    списывает/не меняет тариф; force_unblock_until перекрывает обе причины;
    count_billable_products() = None при отсутствующем/устаревшем StoreProductDB."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "blocking_test.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._session_patch.start()

        self._notify_patch = patch("backend.chatbot.notify_dispatch.dispatch", return_value=None)
        self._notify_patch.start()

        from backend.db.models import Store, StoreTariffDB
        now = _dt.datetime.utcnow()
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(StoreTariffDB(
            store_id="store-a", plan_code="START",
            billing_status="ACTIVE", billing_anchor_day=15,
            period_start=now, period_end=now + _dt.timedelta(days=30),
            next_charge_at=now + _dt.timedelta(days=30), activated_at=now,
        ))
        session.commit()
        session.close()

        from backend.billing import engine, ledger, limits
        self.engine = engine
        self.ledger = ledger
        self.limits = limits

    def tearDown(self):
        self._notify_patch.stop()
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _get_tariff(self):
        from backend.db.models import StoreTariffDB
        session = self.state_helpers._get_sync_db_session()
        tariff = session.get(StoreTariffDB, "store-a")
        session.expunge(tariff)
        session.close()
        return tariff

    def test_exact_threshold_blocks(self):
        self.ledger.debit("store-a", 300_000, "adjustment", idempotency_key="k1")
        self.assertEqual(self.engine.evaluate_block("store-a"), "BLOCKED_BALANCE")

    def test_one_below_threshold_stays_active(self):
        self.ledger.debit("store-a", 299_900, "adjustment", idempotency_key="k2")
        self.assertEqual(self.engine.evaluate_block("store-a"), "ACTIVE")

    def test_product_overage_blocks_without_charging(self):
        with patch.object(self.limits, "count_billable_products", return_value=51):
            status = self.engine.evaluate_block("store-a")
        self.assertEqual(status, "BLOCKED_LIMIT")
        # START лимит 50 товаров превышен — но баланс/тариф не тронуты.
        self.assertEqual(self.ledger.balance("store-a"), 0)
        self.assertEqual(self._get_tariff().plan_code, "START")

    def test_product_count_unknown_does_not_block(self):
        with patch.object(self.limits, "count_billable_products", return_value=None):
            status = self.engine.evaluate_block("store-a")
        self.assertEqual(status, "ACTIVE")

    def test_force_unblock_overrides_balance_and_limit(self):
        self.ledger.debit("store-a", 300_000, "adjustment", idempotency_key="k3")
        from backend.db.models import StoreTariffDB
        session = self.state_helpers._get_sync_db_session()
        tariff = session.get(StoreTariffDB, "store-a")
        tariff.force_unblock_until = _dt.datetime.utcnow() + _dt.timedelta(days=1)
        session.commit()
        session.close()

        with patch.object(self.limits, "count_billable_products", return_value=9999):
            status = self.engine.evaluate_block("store-a")
        self.assertEqual(status, "ACTIVE")

    def test_count_billable_products_none_when_row_missing(self):
        self.assertIsNone(self.limits.count_billable_products("store-a"))

    def test_count_billable_products_none_when_stale(self):
        from backend.db.models import StoreProductDB
        session = self.state_helpers._get_sync_db_session()
        session.add(StoreProductDB(
            store_id="store-a", payload="[]",
            updated_at=_dt.datetime.utcnow() - _dt.timedelta(hours=48),
        ))
        session.commit()
        session.close()
        self.assertIsNone(self.limits.count_billable_products("store-a"))


if __name__ == "__main__":
    unittest.main()
