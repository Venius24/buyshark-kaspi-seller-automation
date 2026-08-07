import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.append(os.getcwd())


class TelegramIdentityTest(unittest.TestCase):
    """
    Тесты идентификации Telegram <-> магазин (docs/TELEGRAM_BOT_PLAN.md, Фаза 1):
    deep-link привязка владельца, приглашение сотрудника без веб-аккаунта,
    резолв активного магазина/роли, отвязка.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "telegram_identity_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

        from backend.chatbot import telegram_identity as ti
        self.ti = ti

        from backend.db.models import Store, User, UserStore
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(Store(id="store-b", merchant_id="m_b", name="Магазин Б"))
        session.add(User(id="owner-1", email="owner@example.com", password_hash="x", is_active=True))
        session.add(UserStore(user_id="owner-1", store_id="store-a", role="OWNER"))
        session.commit()
        session.close()

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # ---- self-link (owner linking their own Telegram) ----

    def test_owner_self_link_success(self):
        token = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", self_user_id="owner-1")
        self.assertIsNotNone(token)

        result = self.ti.consume_link_token(token, tg_user_id=555, tg_username="ownertg", tg_first_name="Owner")
        self.assertTrue(result["success"])
        self.assertEqual(result["store_id"], "store-a")
        self.assertEqual(result["role"], "OWNER")
        self.assertFalse(result["is_new_employee"])

        link = self.ti.get_link(555)
        self.assertEqual(link["user_id"], "owner-1")
        self.assertEqual(link["active_store_id"], "store-a")

    def test_token_cannot_be_reused_beyond_max_uses(self):
        token = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", self_user_id="owner-1", max_uses=1)
        first = self.ti.consume_link_token(token, tg_user_id=1, tg_username=None, tg_first_name="A")
        self.assertTrue(first["success"])
        second = self.ti.consume_link_token(token, tg_user_id=2, tg_username=None, tg_first_name="B")
        self.assertFalse(second["success"])

    def test_unknown_token_rejected(self):
        result = self.ti.consume_link_token("does-not-exist", tg_user_id=1, tg_username=None, tg_first_name=None)
        self.assertFalse(result["success"])

    def test_expired_token_rejected(self):
        from backend.db.models import TelegramLinkTokenDB
        session = self.state_helpers._get_sync_db_session()
        session.add(TelegramLinkTokenDB(
            token="expired-token", user_id="owner-1", store_id="store-a", created_by="owner-1",
            expires_at=datetime.utcnow() - timedelta(minutes=1), max_uses=1, used_count=0,
        ))
        session.commit()
        session.close()

        result = self.ti.consume_link_token("expired-token", tg_user_id=1, tg_username=None, tg_first_name=None)
        self.assertFalse(result["success"])
        self.assertIn("истёк", result["error"])

    # ---- employee invite (no web account) ----

    def test_invite_creates_telegram_only_employee(self):
        token = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", invite_role="MANAGER")
        result = self.ti.consume_link_token(token, tg_user_id=777, tg_username="ivan", tg_first_name="Иван")

        self.assertTrue(result["success"])
        self.assertEqual(result["role"], "MANAGER")
        self.assertTrue(result["is_new_employee"])

        role = self.ti.get_role(self.ti.get_link(777)["user_id"], "store-a")
        self.assertEqual(role, "MANAGER")

    def test_invite_rejects_owner_role(self):
        with self.assertRaises(ValueError):
            self.ti.generate_link_token(store_id="store-a", created_by="owner-1", invite_role="OWNER")

    def test_invite_link_multi_use_onboards_several_employees(self):
        token = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", invite_role="VIEWER", max_uses=3)
        r1 = self.ti.consume_link_token(token, tg_user_id=101, tg_username=None, tg_first_name="Продавец 1")
        r2 = self.ti.consume_link_token(token, tg_user_id=102, tg_username=None, tg_first_name="Продавец 2")
        r3 = self.ti.consume_link_token(token, tg_user_id=103, tg_username=None, tg_first_name="Продавец 3")
        r4 = self.ti.consume_link_token(token, tg_user_id=104, tg_username=None, tg_first_name="Продавец 4")

        self.assertTrue(r1["success"] and r2["success"] and r3["success"])
        self.assertFalse(r4["success"])

    # ---- multi-store / active store ----

    def test_switch_active_store_only_within_own_stores(self):
        from backend.db.models import UserStore
        session = self.state_helpers._get_sync_db_session()
        session.add(UserStore(user_id="owner-1", store_id="store-b", role="OWNER"))
        session.commit()
        session.close()

        token = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", self_user_id="owner-1")
        self.ti.consume_link_token(token, tg_user_id=555, tg_username=None, tg_first_name=None)

        self.assertTrue(self.ti.set_active_store(555, "store-b"))
        self.assertEqual(self.ti.get_active_store_id(555), "store-b")

        self.assertFalse(self.ti.set_active_store(555, "store-not-mine"))

    def test_resolve_bot_access_needs_selection_for_multi_store_without_active_choice(self):
        from backend.db.models import UserStore, TelegramLinkDB
        session = self.state_helpers._get_sync_db_session()
        session.add(UserStore(user_id="owner-1", store_id="store-b", role="OWNER"))
        session.add(TelegramLinkDB(
            tg_user_id=555, user_id="owner-1", active_store_id=None,
            linked_at=datetime.utcnow(), last_seen_at=datetime.utcnow(),
        ))
        session.commit()
        session.close()

        access = self.ti.resolve_bot_access(555)
        self.assertTrue(access["authorized"])
        self.assertTrue(access["needs_store_selection"])
        self.assertEqual(len(access["store_choices"]), 2)

    def test_resolve_bot_access_unauthorized_when_not_linked(self):
        access = self.ti.resolve_bot_access(999999)
        self.assertFalse(access["authorized"])

    # ---- unlink ----

    def test_unlink_self_removes_link(self):
        token = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", self_user_id="owner-1")
        self.ti.consume_link_token(token, tg_user_id=555, tg_username=None, tg_first_name=None)
        self.assertIsNotNone(self.ti.get_link(555))

        self.assertTrue(self.ti.unlink_self(555))
        self.assertIsNone(self.ti.get_link(555))

    def test_owner_can_unlink_employee_and_deletes_telegram_only_user(self):
        token = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", invite_role="MANAGER")
        result = self.ti.consume_link_token(token, tg_user_id=777, tg_username=None, tg_first_name="Иван")
        employee_user_id = result and self.ti.get_link(777)["user_id"]

        ok = self.ti.unlink_member("store-a", 777, requesting_user_id="owner-1")
        self.assertTrue(ok)
        self.assertIsNone(self.ti.get_link(777))

        from backend.db.models import User
        session = self.state_helpers._get_sync_db_session()
        self.assertIsNone(session.get(User, employee_user_id))
        session.close()

    def test_non_owner_cannot_unlink_employee(self):
        token_owner = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", self_user_id="owner-1")
        self.ti.consume_link_token(token_owner, tg_user_id=555, tg_username=None, tg_first_name=None)

        token_employee = self.ti.generate_link_token(store_id="store-a", created_by="owner-1", invite_role="MANAGER")
        result = self.ti.consume_link_token(token_employee, tg_user_id=777, tg_username=None, tg_first_name="Иван")
        employee_user_id = self.ti.get_link(777)["user_id"]

        ok = self.ti.unlink_member("store-a", 555, requesting_user_id=employee_user_id)
        self.assertFalse(ok)

    # ---- legacy fallback (existing .env-based admin_ids mechanism) ----

    def test_resolve_bot_access_falls_back_to_legacy_per_store_admin_ids(self):
        from backend.chatbot import config as chatbot_config
        chatbot_config.save_chatbot_config({"admin_ids": ["424242"]}, store_id="store-a")

        access = self.ti.resolve_bot_access(424242)
        self.assertTrue(access["authorized"])
        self.assertEqual(access["store_id"], "store-a")
        self.assertEqual(access["role"], "OWNER")


if __name__ == "__main__":
    unittest.main()
