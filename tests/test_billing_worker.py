import os
import sys
import shutil
import tempfile
import unittest
import datetime as _dt
from unittest.mock import patch

sys.path.append(os.getcwd())


class BillingWorkerTest(unittest.TestCase):
    """billing_worker.py (Слайс 5): читает stores НАПРЯМУЮ, а не через
    list_active_stores() — обязан видеть PAUSED-магазины и магазины с
    нерасшифровываемым encrypted_api_key (та ловушка, которую список
    активных сторов молча прячет). Одно исключение на магазин не должно
    ронять цикл для остальных."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "worker_test.db")
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
        # Магазин, поставленный на паузу вручную — list_active_stores() его
        # отфильтровал бы, но абонентка должна продолжать списываться.
        session.add(Store(id="paused-store", merchant_id="m_paused", name="На паузе", status="PAUSED", encrypted_api_key="garbage-not-decryptable"))
        session.add(StoreTariffDB(
            store_id="paused-store", plan_code="START",
            billing_status="ACTIVE", billing_anchor_day=15,
            period_start=now - _dt.timedelta(days=31), period_end=now - _dt.timedelta(hours=1),
            next_charge_at=now - _dt.timedelta(hours=1), activated_at=now - _dt.timedelta(days=31),
        ))
        # Обычный активный магазин на DEV — не должен списываться (главный
        # предохранитель миграции 027).
        session.add(Store(id="dev-store", merchant_id="m_dev", name="Дев магазин"))
        session.add(StoreTariffDB(
            store_id="dev-store", plan_code="DEV",
            billing_status="ACTIVE", billing_anchor_day=15,
            period_start=now, period_end=now + _dt.timedelta(days=36500),
            next_charge_at=now + _dt.timedelta(days=36500), activated_at=now,
        ))
        session.commit()
        session.close()

        from backend.billing import ledger
        self.ledger = ledger

        from backend.services import billing_worker
        self.billing_worker = billing_worker

    def tearDown(self):
        self._notify_patch.stop()
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_list_all_store_ids_includes_paused_and_undecryptable(self):
        ids = self.billing_worker._list_all_store_ids()
        self.assertIn("paused-store", ids)
        self.assertIn("dev-store", ids)

    def test_cycle_charges_paused_store_and_skips_dev(self):
        import asyncio
        asyncio.run(self.billing_worker.run_billing_cycle())
        self.assertEqual(self.ledger.balance("paused-store"), -499_000)
        self.assertEqual(self.ledger.balance("dev-store"), 0)

    def test_one_store_failure_does_not_stop_cycle(self):
        import asyncio
        from backend.billing import engine as billing_engine

        orig_charge = billing_engine.charge_period

        def flaky_charge(store_id):
            if store_id == "paused-store":
                raise RuntimeError("boom")
            return orig_charge(store_id)

        with patch.object(billing_engine, "charge_period", side_effect=flaky_charge):
            asyncio.run(self.billing_worker.run_billing_cycle())

        # dev-store не задета флейком (не списывается никогда), но сам факт,
        # что цикл дошёл до конца и не выбросил исключение наружу — это и
        # есть проверка изоляции сбоя одного магазина.
        self.assertEqual(self.ledger.balance("dev-store"), 0)


if __name__ == "__main__":
    unittest.main()
