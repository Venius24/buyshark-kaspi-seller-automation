import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class TelegramBotAssemblyHelpersTest(unittest.TestCase):
    """
    Ф2/Ф3 (docs/TELEGRAM_BOT_PLAN.md): вспомогательные sync-функции бота,
    которыми управляются карточки правил Автосборки (⚙️ Автосборка,
    ☀️ Утренний сбор) — сетка по всем видам доставки с дефолтами,
    upsert-toggle enabled/этапов/dry_run.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 регистрирует ORM-классы на Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "assembly_helpers_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

        from backend.db.models import Store
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.commit()
        session.close()

        from backend.chatbot import handlers as bot_handlers
        self.bh = bot_handlers

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_load_delivery_rules_returns_defaults_for_unconfigured_store(self):
        from backend.routers.auto_assembly import DELIVERY_TYPES
        rules = self.bh._load_delivery_rules("store-a")
        self.assertEqual(len(rules), len(DELIVERY_TYPES))
        self.assertTrue(all(r["enabled"] is False for r in rules))
        self.assertTrue(all(r["stage_accept"] is False for r in rules))

    def test_toggle_rule_enabled_creates_row_and_flips(self):
        new_state = self.bh._toggle_rule_enabled("store-a", "Express")
        self.assertTrue(new_state)

        rules = self.bh._load_delivery_rules("store-a")
        express = next(r for r in rules if r["delivery_type"] == "Express")
        self.assertTrue(express["enabled"])

        new_state_2 = self.bh._toggle_rule_enabled("store-a", "Express")
        self.assertFalse(new_state_2)

    def test_toggle_rule_field_flips_stage_and_creates_row(self):
        new_state = self.bh._toggle_rule_field("store-a", "Postomat", "stage_waybill")
        self.assertTrue(new_state)

        rules = self.bh._load_delivery_rules("store-a")
        postomat = next(r for r in rules if r["delivery_type"] == "Postomat")
        self.assertTrue(postomat["stage_waybill"])
        self.assertFalse(postomat["enabled"])  # toggling a stage не включает правило само по себе

    def test_toggle_rule_field_rejects_unknown_field(self):
        result = self.bh._toggle_rule_field("store-a", "Express", "not_a_real_field")
        self.assertIsNone(result)

    def test_toggling_multiple_delivery_types_is_independent(self):
        self.bh._toggle_rule_enabled("store-a", "Express")
        self.bh._toggle_rule_field("store-a", "Today", "stage_accept")

        rules = {r["delivery_type"]: r for r in self.bh._load_delivery_rules("store-a")}
        self.assertTrue(rules["Express"]["enabled"])
        self.assertFalse(rules["Today"]["enabled"])
        self.assertTrue(rules["Today"]["stage_accept"])
        self.assertFalse(rules["Express"]["stage_accept"])


if __name__ == "__main__":
    unittest.main()
