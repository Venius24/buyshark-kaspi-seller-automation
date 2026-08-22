import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class FeatureFlagsTest(unittest.TestCase):
    """Вкладка "Глобальное управление" — store_feature_flags должны быть
    изолированы per-store, отсутствующая строка = все включены (дефолт),
    частичный set_flags не трогает остальные поля, кэш инвалидируется на set."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "feature_flags_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

        from backend import feature_flags
        self.feature_flags = feature_flags
        feature_flags._cache.clear()

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_missing_row_defaults_to_all_enabled(self):
        self.assertEqual(
            self.feature_flags.get_flags("store-A"),
            {"dumping_enabled": True, "stock_groups_enabled": True, "autoassembly_enabled": True},
        )

    def test_partial_update_does_not_touch_other_fields(self):
        self.feature_flags.set_flags("store-A", dumping_enabled=False)
        flags = self.feature_flags.get_flags("store-A")
        self.assertFalse(flags["dumping_enabled"])
        self.assertTrue(flags["stock_groups_enabled"])
        self.assertTrue(flags["autoassembly_enabled"])

    def test_flags_isolated_between_stores(self):
        self.feature_flags.set_flags("store-A", dumping_enabled=False)
        self.assertFalse(self.feature_flags.is_dumping_enabled("store-A"))
        self.assertTrue(self.feature_flags.is_dumping_enabled("store-B"))

    def test_set_flags_invalidates_cache(self):
        self.assertTrue(self.feature_flags.is_stock_groups_enabled("store-A"))
        self.feature_flags.set_flags("store-A", stock_groups_enabled=False)
        self.assertFalse(self.feature_flags.is_stock_groups_enabled("store-A"))

    def test_none_values_are_ignored(self):
        self.feature_flags.set_flags("store-A", dumping_enabled=False)
        self.feature_flags.set_flags("store-A", dumping_enabled=None, stock_groups_enabled=False)
        flags = self.feature_flags.get_flags("store-A")
        self.assertFalse(flags["dumping_enabled"])
        self.assertFalse(flags["stock_groups_enabled"])


if __name__ == "__main__":
    unittest.main()
