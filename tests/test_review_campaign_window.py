import os
import sys
import shutil
import tempfile
import unittest
import datetime as _dt
from unittest.mock import patch

sys.path.append(os.getcwd())


class ReviewCampaignShouldSendTest(unittest.TestCase):
    """
    Правило окна кампании «Отзыв» (Фаза 5.1, 2026-08-11, со слов пользователя):
    без "до" и без чекбокса — РОВНО N дней; с "до" — диапазон [N, до]; чекбокс
    "слать пропущенным" (без "до") — N дней и старше.
    """

    def setUp(self):
        from backend.chatbot.review_campaign import _should_send
        self._should_send = _should_send

    def test_exact_day_only_without_to_or_overdue(self):
        self.assertFalse(self._should_send(2, from_days=3, to_days=None, send_overdue=False))
        self.assertTrue(self._should_send(3, from_days=3, to_days=None, send_overdue=False))
        self.assertFalse(self._should_send(4, from_days=3, to_days=None, send_overdue=False))

    def test_range_when_to_days_set(self):
        self.assertFalse(self._should_send(2, from_days=3, to_days=7, send_overdue=False))
        self.assertTrue(self._should_send(3, from_days=3, to_days=7, send_overdue=False))
        self.assertTrue(self._should_send(7, from_days=3, to_days=7, send_overdue=False))
        self.assertFalse(self._should_send(8, from_days=3, to_days=7, send_overdue=False))

    def test_overdue_checkbox_widens_to_open_ended(self):
        self.assertFalse(self._should_send(2, from_days=3, to_days=None, send_overdue=True))
        self.assertTrue(self._should_send(3, from_days=3, to_days=None, send_overdue=True))
        self.assertTrue(self._should_send(30, from_days=3, to_days=None, send_overdue=True))

    def test_to_days_takes_precedence_over_overdue(self):
        # "до" задано — диапазон, чекбокс тут ничего не расширяет.
        self.assertFalse(self._should_send(10, from_days=3, to_days=7, send_overdue=True))


class ReviewCampaignScanTest(unittest.TestCase):
    """Сквозной тест scan_and_enqueue_review_campaign на реальной (тестовой)
    БД — проверяет фильтр по review_eligible (холодный бэкафилл) и по окну
    дней, дедуп через enqueue_outbound."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "review_scan_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

        # review_campaign.py/prompt.py импортируют _get_sync_db_session
        # локально внутри функций (патч выше их покрывает), но
        # chatbot/outbound.py импортирует имя один раз на уровне модуля —
        # патчим и его, иначе enqueue_outbound уйдёт в реальную БД.
        from backend.chatbot import outbound as bot_outbound
        self._outbound_session_patch = patch.object(
            bot_outbound, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._outbound_session_patch.start()

        from backend.db.models import Store
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.commit()
        session.close()

    def tearDown(self):
        self._outbound_session_patch.stop()
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _add_order(self, order_id, days_ago, review_eligible=True, status="COMPLETED", state="ARCHIVE"):
        from backend.db.models import SalesOrderDB
        session = self.state_helpers._get_sync_db_session()
        session.add(SalesOrderDB(
            store_id="store-a",
            order_internal_id=f"internal-{order_id}",
            order_code=order_id,
            created_at=_dt.datetime.utcnow(),
            total_price=1000,
            status=status,
            state=state,
            entries_payload="[]",
            ingested_at=_dt.datetime.utcnow() - _dt.timedelta(days=days_ago),
            review_eligible=review_eligible,
        ))
        session.commit()
        session.close()

    def test_default_window_matches_exact_day_only(self):
        from backend.chatbot.review_campaign import scan_and_enqueue_review_campaign
        from backend.db.models import OutboundQueueDB
        from sqlalchemy import select

        self._add_order("ORD-2D", days_ago=2)
        self._add_order("ORD-4D", days_ago=4)  # дефолт profile: from_days=4

        queued = scan_and_enqueue_review_campaign("store-a")
        self.assertEqual(queued, 1)

        session = self.state_helpers._get_sync_db_session()
        rows = session.execute(select(OutboundQueueDB).where(OutboundQueueDB.store_id == "store-a")).scalars().all()
        session.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].order_id, "ORD-4D")

    def test_cold_start_orders_excluded_from_scan(self):
        from backend.chatbot.review_campaign import scan_and_enqueue_review_campaign

        self._add_order("ORD-COLD", days_ago=4, review_eligible=False)

        queued = scan_and_enqueue_review_campaign("store-a")
        self.assertEqual(queued, 0)

    def test_rescan_does_not_duplicate(self):
        from backend.chatbot.review_campaign import scan_and_enqueue_review_campaign

        self._add_order("ORD-4D", days_ago=4)
        first = scan_and_enqueue_review_campaign("store-a")
        second = scan_and_enqueue_review_campaign("store-a")
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_custom_window_and_overdue_flag(self):
        from backend.chatbot import prompt as bot_prompt
        from backend.chatbot.review_campaign import scan_and_enqueue_review_campaign

        bot_prompt.save_profile("store-a", {
            "review_delay_from_days": 3,
            "review_send_overdue": True,
        })

        self._add_order("ORD-3D", days_ago=3)
        self._add_order("ORD-10D", days_ago=10)
        self._add_order("ORD-1D", days_ago=1)

        queued = scan_and_enqueue_review_campaign("store-a")
        self.assertEqual(queued, 2)  # 3D и 10D попадают (>=3), 1D — нет


if __name__ == "__main__":
    unittest.main()
