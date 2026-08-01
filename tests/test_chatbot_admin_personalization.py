import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class ChatbotAdminPersonalizationTest(unittest.TestCase):
    """
    Единый Telegram-бот обслуживает админов НЕСКОЛЬКИХ магазинов сразу
    (см. HANDOFF 3.14/task про per-store логику в едином боте — полный
    рефакторинг на отдельные боты отложен пользователем). Эти тесты
    проверяют реверс-résolve "какие магазины у этого admin_id" и то, что
    имя магазина корректно подставляется вместо голого store_id/merchant_id.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "chatbot_admin_test.db")
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

        from backend.db.models import Store
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(Store(id="store-b", merchant_id="m_b", name="Магазин Б"))
        session.add(Store(id="store-c", merchant_id="m_c", name="Магазин В"))
        session.commit()
        session.close()

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_get_store_name_resolves_real_name(self):
        self.assertEqual(self.config.get_store_name("store-a"), "Магазин А")

    def test_get_store_name_falls_back_to_id_for_unknown_store(self):
        self.assertEqual(self.config.get_store_name("does-not-exist"), "does-not-exist")

    def test_admin_with_one_store_resolves_only_that_store(self):
        self.config.save_chatbot_config({"admin_ids": ["111", "222"]}, store_id="store-a")
        self.config.save_chatbot_config({"admin_ids": ["333"]}, store_id="store-b")

        self.assertEqual(self.config.get_store_ids_for_admin("111"), ["store-a"])
        self.assertEqual(self.config.get_store_ids_for_admin("333"), ["store-b"])

    def test_admin_shared_across_two_stores_resolves_both(self):
        self.config.save_chatbot_config({"admin_ids": ["999"]}, store_id="store-a")
        self.config.save_chatbot_config({"admin_ids": ["999"]}, store_id="store-b")
        self.config.save_chatbot_config({"admin_ids": ["111"]}, store_id="store-c")

        result = sorted(self.config.get_store_ids_for_admin("999"))
        self.assertEqual(result, ["store-a", "store-b"])

    def test_admin_with_no_stores_resolves_empty(self):
        self.config.save_chatbot_config({"admin_ids": ["111"]}, store_id="store-a")
        self.assertEqual(self.config.get_store_ids_for_admin("does-not-manage-anything"), [])


if __name__ == "__main__":
    unittest.main()
