import os
import sys
import shutil
import tempfile
import unittest
import datetime as _dt
from unittest.mock import patch

sys.path.append(os.getcwd())


class BillingEngineTest(unittest.TestCase):
    """charge_period()/evaluate_block() (Слайс 5): DEV не списывается и не
    блокируется (главный предохранитель миграции 027), продление списывает
    ДВЕ строки по каталогу (base/prompt — дневные кредиты теперь зашиты в
    цену тарифа, отдельной строки charge_credit_addon больше нет), повтор
    без сдвига периода не дублирует, кламп anchor-day не дрейфует."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "engine_test.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._session_patch.start()

        # engine.py читает prompt_tier через backend.chatbot.prompt.get_profile —
        # изолируем его, чтобы тест не тянул chatbot-профиль (не создаём его тут).
        self._prompt_patch = patch(
            "backend.chatbot.prompt.get_profile", return_value={"prompt_tier": "band0"}
        )
        self._prompt_patch.start()

        # Уведомления о блокировке используют dispatch() из event loop —
        # в sync-тестах цикла нет, но подстрахуемся явным patch на no-op.
        self._notify_patch = patch(
            "backend.chatbot.notify_dispatch.dispatch", return_value=None
        )
        self._notify_patch.start()

        from backend.db.models import Store
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.commit()
        session.close()

        from backend.billing import engine, ledger
        self.engine = engine
        self.ledger = ledger

    def tearDown(self):
        self._notify_patch.stop()
        self._prompt_patch.stop()
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _make_tariff(self, plan_code="START", **overrides):
        from backend.db.models import StoreTariffDB
        now = _dt.datetime.utcnow()
        defaults = dict(
            store_id="store-a", plan_code=plan_code,
            billing_status="ACTIVE", billing_anchor_day=15,
            period_start=now - _dt.timedelta(days=31), period_end=now - _dt.timedelta(hours=1),
            next_charge_at=now - _dt.timedelta(hours=1), activated_at=now - _dt.timedelta(days=31),
        )
        defaults.update(overrides)
        session = self.state_helpers._get_sync_db_session()
        session.add(StoreTariffDB(**defaults))
        session.add_all([])
        session.commit()
        session.close()

    def _get_tariff(self):
        from backend.db.models import StoreTariffDB
        session = self.state_helpers._get_sync_db_session()
        tariff = session.get(StoreTariffDB, "store-a")
        session.expunge(tariff)
        session.close()
        return tariff

    def test_dev_plan_never_charged(self):
        self._make_tariff(plan_code="DEV")
        charged = self.engine.charge_period("store-a")
        self.assertEqual(charged, 0)
        self.assertEqual(self.ledger.balance("store-a"), 0)

    def test_dev_plan_never_blocked_even_with_negative_balance(self):
        self._make_tariff(plan_code="DEV")
        self.ledger.debit("store-a", 1_000_000, "adjustment", idempotency_key="neg")
        status = self.engine.evaluate_block("store-a")
        self.assertEqual(status, "ACTIVE")

    def test_charge_writes_only_base_and_prompt_lines_not_credits(self):
        self._make_tariff(plan_code="START")
        charged = self.engine.charge_period("store-a")
        self.assertEqual(charged, 1)
        txns = self.ledger.list_transactions("store-a")
        kinds = {t["kind"] for t in txns}
        self.assertIn("charge_subscription", kinds)
        self.assertNotIn("charge_credit_addon", kinds)
        # START имеет базовую цену > 0, промпт по умолчанию 0 — значит
        # только charge_subscription должен попасть в леджер (не 3 строки,
        # как раньше, когда была ещё отдельная кредитная надбавка).
        self.assertEqual(len(txns), 1)
        self.assertEqual(self.ledger.balance("store-a"), -640_000)

    def test_charge_writes_exactly_two_lines_when_prompt_band_charges(self):
        self._make_tariff(plan_code="START")
        with patch("backend.chatbot.prompt.get_profile", return_value={"prompt_tier": "band1"}):
            self.engine.charge_period("store-a")
        txns = self.ledger.list_transactions("store-a")
        kinds = {t["kind"] for t in txns}
        self.assertEqual(kinds, {"charge_subscription", "charge_prompt_addon"})
        self.assertEqual(len(txns), 2)
        self.assertEqual(self.ledger.balance("store-a"), -740_000)

    def test_charge_advances_period_and_next_charge_moves_forward(self):
        self._make_tariff(plan_code="START")
        old_tariff = self._get_tariff()
        self.engine.charge_period("store-a")
        new_tariff = self._get_tariff()
        self.assertGreater(new_tariff.next_charge_at, old_tariff.next_charge_at)
        self.assertEqual(new_tariff.period_start, old_tariff.period_end)

    def test_not_overdue_charges_nothing(self):
        now = _dt.datetime.utcnow()
        self._make_tariff(plan_code="START", next_charge_at=now + _dt.timedelta(days=10))
        charged = self.engine.charge_period("store-a")
        self.assertEqual(charged, 0)
        self.assertEqual(self.ledger.balance("store-a"), 0)

    def test_anchor_day_clamps_end_of_month(self):
        result = self.engine._add_month(_dt.datetime(2026, 1, 31), anchor_day=31)
        self.assertEqual(result, _dt.datetime(2026, 2, 28))
        result2 = self.engine._add_month(result, anchor_day=31)
        self.assertEqual(result2, _dt.datetime(2026, 3, 31))

    def test_custom_monthly_price_overrides_catalog(self):
        self._make_tariff(plan_code="GIGA", custom_monthly_price_minor=5_000_000)
        self.engine.charge_period("store-a")
        self.assertEqual(self.ledger.balance("store-a"), -5_000_000)

    def test_negative_balance_blocks_store(self):
        self._make_tariff(plan_code="START")
        self.ledger.debit("store-a", 300_000, "adjustment", idempotency_key="over-threshold")
        status = self.engine.evaluate_block("store-a")
        self.assertEqual(status, "BLOCKED_BALANCE")

    def test_balance_just_above_threshold_stays_active(self):
        self._make_tariff(plan_code="START")
        self.ledger.debit("store-a", 299_900, "adjustment", idempotency_key="under-threshold")
        status = self.engine.evaluate_block("store-a")
        self.assertEqual(status, "ACTIVE")

    def test_topup_reactivates_blocked_store(self):
        self._make_tariff(plan_code="START")
        self.ledger.debit("store-a", 300_000, "adjustment", idempotency_key="block-it")
        self.engine.evaluate_block("store-a")
        self.assertEqual(self._get_tariff().billing_status, "BLOCKED_BALANCE")
        self.ledger.credit("store-a", 400_000, "topup_manual", idempotency_key="unblock-it")
        status = self.engine.evaluate_block("store-a")
        self.assertEqual(status, "ACTIVE")


if __name__ == "__main__":
    unittest.main()
