import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class ChatReloginButtonTest(unittest.TestCase):
    """Кнопка «Восстановить» под алертом «сессия чата истекла»: последний
    номер SMS-входа сохраняется per-store и подставляется в кнопку."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "relogin_test.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        # Порядок как в проде: notifications ↔ chat_client/kaspi — цикл импорта,
        # прямой первый импорт notifications падает.
        import backend.chatbot.worker  # noqa: F401
        from backend import state_helpers
        self._patch = patch.object(state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine))
        self._patch.start()

        from backend.db.models import Store
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.commit()
        session.close()

    def tearDown(self):
        self._patch.stop()
        self._engine.dispose()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_phone_roundtrip_and_alert_flag_untouched(self):
        from backend.chatbot import config
        self.assertIsNone(config.get_chat_login_phone("store-a"))
        config.set_chat_login_phone("store-a", "+77011234567")
        self.assertEqual(config.get_chat_login_phone("store-a"), "+77011234567")
        config.set_cookie_alert_sent("store-a", True)
        self.assertEqual(config.get_chat_login_phone("store-a"), "+77011234567")

    def test_mask_phone(self):
        from backend.chatbot.notifications import mask_phone
        self.assertEqual(mask_phone("+7 701 123 45 67"), "+7•••••4567")

    def test_keyboard_carries_store_and_masked_phone(self):
        from backend.chatbot import bot_instance, config
        from backend.chatbot.notifications import chat_relogin_keyboard
        if not bot_instance.HAS_AIOGRAM:
            self.skipTest("aiogram не установлен")
        btn = chat_relogin_keyboard("store-a").inline_keyboard[0][0]
        self.assertEqual(btn.callback_data, "chatcookie_quick:store-a")
        self.assertNotIn("•", btn.text)
        config.set_chat_login_phone("store-a", "+77011234567")
        btn = chat_relogin_keyboard("store-a").inline_keyboard[0][0]
        self.assertIn("+7•••••4567", btn.text)


if __name__ == "__main__":
    unittest.main()
