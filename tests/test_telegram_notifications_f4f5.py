import os
import sys
import shutil
import tempfile
import unittest
import datetime
from unittest.mock import patch

sys.path.append(os.getcwd())


class TelegramNotificationsAndAuditTest(unittest.TestCase):
    """
    Ф4 (уведомления) / Ф5 (аудит) — docs/TELEGRAM_BOT_PLAN.md §4.1, §4.4, §6:
    персональные настройки уведомлений per (tg_user_id, store_id), резолв
    получателей магазина через UserStore+TelegramLinkDB, тихие часы, аудит
    мутирующих действий из бота.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "notif_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

        from backend.db.models import Store, User, UserStore, TelegramLinkDB
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(Store(id="store-b", merchant_id="m_b", name="Магазин Б"))
        session.add(User(id="owner-1", email="owner@example.com", password_hash="x", is_active=True))
        session.add(User(id="mgr-1", email="mgr@example.com", password_hash="x", is_active=True))
        session.add(UserStore(user_id="owner-1", store_id="store-a", role="OWNER"))
        session.add(UserStore(user_id="mgr-1", store_id="store-a", role="MANAGER"))
        session.add(TelegramLinkDB(tg_user_id=111, user_id="owner-1", active_store_id="store-a",
                                    linked_at=datetime.datetime.utcnow(), last_seen_at=datetime.datetime.utcnow()))
        session.add(TelegramLinkDB(tg_user_id=222, user_id="mgr-1", active_store_id="store-a",
                                    linked_at=datetime.datetime.utcnow(), last_seen_at=datetime.datetime.utcnow()))
        session.commit()
        session.close()

        from backend.chatbot import notification_prefs, notify_dispatch, audit
        self.notification_prefs = notification_prefs
        self.notify_dispatch = notify_dispatch
        self.audit = audit

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # ---- notification_prefs ----

    def test_defaults_match_plan(self):
        prefs = self.notification_prefs.get_prefs(111, "store-a")
        self.assertTrue(prefs["chats"])
        self.assertTrue(prefs["problems"])
        self.assertFalse(prefs["money"])  # выкл по умолчанию — самый спамный канал
        self.assertIsNone(prefs["quiet_from"])

    def test_toggle_group_creates_row_and_flips(self):
        new_state = self.notification_prefs.toggle_group(111, "store-a", "money")
        self.assertTrue(new_state)
        self.assertTrue(self.notification_prefs.get_prefs(111, "store-a")["money"])

        new_state_2 = self.notification_prefs.toggle_group(111, "store-a", "money")
        self.assertFalse(new_state_2)

    def test_toggle_group_rejects_unknown_group(self):
        self.assertIsNone(self.notification_prefs.toggle_group(111, "store-a", "bogus"))

    def test_prefs_are_scoped_per_store(self):
        self.notification_prefs.toggle_group(111, "store-a", "money")
        prefs_other_store = self.notification_prefs.get_prefs(111, "store-b")
        self.assertFalse(prefs_other_store["money"])  # тот же tg_user_id, другой магазин — не задето

    def test_set_quiet_hours(self):
        self.notification_prefs.set_quiet_hours(111, "store-a", "23:00", "08:00")
        prefs = self.notification_prefs.get_prefs(111, "store-a")
        self.assertEqual(prefs["quiet_from"], "23:00")
        self.assertEqual(prefs["quiet_to"], "08:00")

        self.notification_prefs.set_quiet_hours(111, "store-a", None, None)
        prefs = self.notification_prefs.get_prefs(111, "store-a")
        self.assertIsNone(prefs["quiet_from"])

    # ---- notify_dispatch recipients ----

    def test_recipients_for_store_includes_all_roles(self):
        recipients = self.notify_dispatch._recipients_for_store("store-a")
        self.assertEqual(set(recipients), {111, 222})

    def test_recipients_for_store_empty_for_unrelated_store(self):
        recipients = self.notify_dispatch._recipients_for_store("store-b")
        self.assertEqual(recipients, [])

    def test_in_quiet_hours_overnight_span(self):
        fn = self.notify_dispatch._in_quiet_hours
        late_night = datetime.datetime(2026, 1, 1, 23, 30)
        early_morning = datetime.datetime(2026, 1, 1, 5, 0)
        midday = datetime.datetime(2026, 1, 1, 13, 0)
        self.assertTrue(fn("23:00", "08:00", late_night))
        self.assertTrue(fn("23:00", "08:00", early_morning))
        self.assertFalse(fn("23:00", "08:00", midday))

    def test_in_quiet_hours_disabled_when_unset(self):
        fn = self.notify_dispatch._in_quiet_hours
        self.assertFalse(fn(None, None, datetime.datetime(2026, 1, 1, 23, 30)))

    # ---- audit log ----

    def test_log_action_persists_entry(self):
        self.audit.log_action("store-a", 111, "aa_toggle", "Express -> ON")

        from sqlalchemy import select
        from backend.db.models import TelegramAuditLogDB
        session = self.state_helpers._get_sync_db_session()
        rows = session.scalars(select(TelegramAuditLogDB).where(TelegramAuditLogDB.store_id == "store-a")).all()
        session.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tg_user_id, 111)
        self.assertEqual(rows[0].action, "aa_toggle")
        self.assertEqual(rows[0].detail, "Express -> ON")


if __name__ == "__main__":
    unittest.main()
