import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


def make_config_dict(min_price=100.0):
    return {
        "min_price": min_price,
        "max_price": 0.0,
        "dump_step": 10.0,
        "enabled": True,
        "mode": "TOP_1",
        "target_position": 1,
        "max_delivery_days": None,
        "min_seller_rating": None,
        "same_city_only": False,
        "blacklisted_merchants": [],
        "city_code": "750000000",
    }


class DumpingConfigsStoreScopingTest(unittest.TestCase):
    """
    Phase 4b: _load_dumping_configs/_save_dumping_configs now accept an
    optional store_id. These tests are the evidence that scoped reads never
    leak another store's (or legacy, unscoped) data — the exact bug class
    Phase 4 exists to close.
    """

    def setUp(self):
        # backend.db.database.ASYNC_DB_URL is a module-level constant fixed at
        # first import (whichever DATABASE_URL was active then) — patching the
        # env var here has no effect on it, and _get_sync_db_session() derives
        # its engine from that constant, not a fresh os.getenv() read. So we
        # patch the sync-session factory itself to point at an isolated file.
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "scoping_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_scoped_save_then_scoped_load_same_store(self):
        self.state_helpers._save_dumping_configs({"SKU-A": make_config_dict(100)}, store_id="store-A")
        configs = self.state_helpers._load_dumping_configs(store_id="store-A")
        self.assertIn("SKU-A", configs)
        self.assertEqual(configs["SKU-A"]["min_price"], 100.0)

    def test_scoped_load_does_not_see_other_stores_data(self):
        self.state_helpers._save_dumping_configs({"SKU-A": make_config_dict(100)}, store_id="store-A")
        configs_b = self.state_helpers._load_dumping_configs(store_id="store-B")
        self.assertEqual(configs_b, {})

    def test_scoped_load_does_not_see_legacy_unscoped_rows(self):
        # Legacy caller writes with no store_id (store_id stays NULL)
        self.state_helpers._save_dumping_configs({"SKU-LEGACY": make_config_dict(50)})
        configs_scoped = self.state_helpers._load_dumping_configs(store_id="store-A")
        self.assertEqual(configs_scoped, {})

    def test_unscoped_legacy_load_still_sees_everything(self):
        """Backward compatibility: a caller not yet migrated (store_id=None)
        must keep seeing the exact same unfiltered view it always has."""
        self.state_helpers._save_dumping_configs({"SKU-A": make_config_dict(100)}, store_id="store-A")
        self.state_helpers._save_dumping_configs({"SKU-B": make_config_dict(200)}, store_id="store-B")
        self.state_helpers._save_dumping_configs({"SKU-LEGACY": make_config_dict(300)})

        legacy_view = self.state_helpers._load_dumping_configs()
        self.assertEqual(set(legacy_view.keys()), {"SKU-A", "SKU-B", "SKU-LEGACY"})

    def test_scoped_save_tags_a_previously_legacy_row(self):
        """A row created before store scoping existed (store_id NULL) gets
        correctly tagged once a scoped caller updates it."""
        self.state_helpers._save_dumping_configs({"SKU-X": make_config_dict(10)})  # legacy write
        self.assertEqual(self.state_helpers._load_dumping_configs(store_id="store-A"), {})

        self.state_helpers._save_dumping_configs({"SKU-X": make_config_dict(20)}, store_id="store-A")
        scoped = self.state_helpers._load_dumping_configs(store_id="store-A")
        self.assertEqual(scoped["SKU-X"]["min_price"], 20.0)

    def test_no_db_session_scoped_load_returns_empty_not_shared_file(self):
        """If the DB is unreachable, a scoped caller must never fall back to
        the shared legacy JSON file — that file mixes every store's data."""
        os.makedirs("states", exist_ok=True)
        with open("states/dumping_configs.json", "w", encoding="utf-8") as f:
            import json
            json.dump({"SOMEONE-ELSES-SKU": make_config_dict(999)}, f)

        with patch.object(self.state_helpers, "_get_sync_db_session", return_value=None):
            scoped = self.state_helpers._load_dumping_configs(store_id="store-A")
            self.assertEqual(scoped, {})

            legacy = self.state_helpers._load_dumping_configs()
            self.assertIn("SOMEONE-ELSES-SKU", legacy)


if __name__ == "__main__":
    unittest.main()
