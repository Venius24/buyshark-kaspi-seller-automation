import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class ChatbotStoreScopingTest(unittest.TestCase):
    """
    Слайс 4: PROCESSED_CHATS/COOKIE_ALERT_SENT/enabled-флаг чат-бота были
    общими на все магазины — один магазин выключал бота всем, блокировка
    HUMAN_OVERRIDE одного стора была видна/снималась из другого. Эти тесты —
    доказательство, что chat_states/chatbot_settings теперь изолированы per-store.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "chatbot_scoping_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

        from backend.chatbot import config
        self.config = config
        # Reset in-memory legacy/anti-spam state between tests (module-level dicts).
        config._legacy_chat_state.clear()
        config._legacy_cookie_alert_sent.clear()

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_human_override_isolated_between_stores(self):
        self.config.set_chat_human_override("store-A", "chat-1")
        self.assertTrue(self.config.is_chat_human_override("store-A", "chat-1"))
        self.assertFalse(self.config.is_chat_human_override("store-B", "chat-1"))

    def test_clear_human_override_only_affects_its_own_store(self):
        self.config.set_chat_human_override("store-A", "chat-1")
        self.config.set_chat_human_override("store-B", "chat-1")

        cleared = self.config.clear_chat_human_override("store-A", "chat-1")
        self.assertTrue(cleared)
        self.assertFalse(self.config.is_chat_human_override("store-A", "chat-1"))
        self.assertTrue(self.config.is_chat_human_override("store-B", "chat-1"))

    def test_mark_replied_does_not_leak_across_stores(self):
        self.config.mark_chat_replied("store-A", "chat-1", "msg-100")
        self.assertEqual(self.config.get_chat_last_replied("store-A", "chat-1"), "msg-100")
        self.assertIsNone(self.config.get_chat_last_replied("store-B", "chat-1"))

    def test_batch_chat_states_uses_one_scoped_query(self):
        from sqlalchemy import event

        self.config.mark_chat_replied("store-A", "chat-1", "msg-100")
        self.config.set_chat_human_override("store-A", "chat-2")
        self.config.set_awaiting_review_reply("store-A", "chat-3")
        self.config.mark_chat_replied("store-B", "chat-1", "foreign-msg")
        queries = []

        def count_query(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().lower().startswith("select") and "chat_states" in statement:
                queries.append(statement)

        event.listen(self._sync_engine, "before_cursor_execute", count_query)
        try:
            states = self.config.get_chat_states(
                "store-A", ["chat-1", "chat-2", "chat-3", "chat-missing", "chat-1"]
            )
        finally:
            event.remove(self._sync_engine, "before_cursor_execute", count_query)

        self.assertEqual(len(queries), 1)
        self.assertEqual(states["chat-1"]["last_replied_msg_id"], "msg-100")
        self.assertTrue(states["chat-2"]["human_override"])
        self.assertTrue(states["chat-3"]["awaiting_review_reply"])
        self.assertNotIn("chat-missing", states)

    def test_batch_chat_states_legacy_awaiting_flag(self):
        self.config._legacy_awaiting_review_reply.add("chat-review")
        try:
            states = self.config.get_chat_states(None, ["chat-review", "chat-missing"])
        finally:
            self.config._legacy_awaiting_review_reply.clear()
        self.assertTrue(states["chat-review"]["awaiting_review_reply"])
        self.assertNotIn("chat-missing", states)

    def test_enabled_flag_is_independent_per_store(self):
        self.config.save_chatbot_config({"enabled": False}, store_id="store-A")
        self.config.save_chatbot_config({"enabled": True}, store_id="store-B")

        self.assertFalse(self.config.load_chatbot_config(store_id="store-A")["enabled"])
        self.assertTrue(self.config.load_chatbot_config(store_id="store-B")["enabled"])

    def test_cookie_alert_sent_is_independent_per_store(self):
        self.config.set_cookie_alert_sent("store-A", True)
        self.assertTrue(self.config.is_cookie_alert_sent("store-A"))
        self.assertFalse(self.config.is_cookie_alert_sent("store-B"))
        self.assertTrue(self.config.any_cookie_alert_sent())

    def test_count_and_list_blocked_chats_scoped_per_store(self):
        self.config.set_chat_human_override("store-A", "chat-1")
        self.config.set_chat_human_override("store-A", "chat-2")
        self.config.set_chat_human_override("store-B", "chat-3")

        self.assertEqual(self.config.count_blocked_chats("store-A"), 2)
        self.assertEqual(self.config.count_blocked_chats("store-B"), 1)
        self.assertEqual(set(self.config.list_blocked_chat_ids("store-A")), {"chat-1", "chat-2"})

    def test_clear_human_override_any_store_finds_correct_owner(self):
        self.config.set_chat_human_override("store-B", "chat-only-in-b")
        owner = self.config.clear_human_override_any_store("chat-only-in-b")
        self.assertEqual(owner, "store-B")
        self.assertFalse(self.config.is_chat_human_override("store-B", "chat-only-in-b"))

    def test_clear_human_override_any_store_returns_none_when_not_blocked(self):
        self.assertIsNone(self.config.clear_human_override_any_store("never-blocked-chat"))


if __name__ == "__main__":
    unittest.main()
