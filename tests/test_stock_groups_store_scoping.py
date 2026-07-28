import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


def make_group_dict(group_name="Test Group", shared_stock=10):
    return {
        "group_name": group_name,
        "shared_stock": shared_stock,
        "linked_skus": ["SKU-1", "SKU-2"],
        "external_code": None,
        "is_active": True,
        "dumping_config": None,
        "status": "synced",
        "created_at": "2026-07-28 00:00:00",
        "updated_at": "2026-07-28 00:00:00",
        "last_synced_at": None,
        "last_sync_log": None,
        "sync_logs": [],
    }


class MasterStockGroupsStoreScopingTest(unittest.TestCase):
    """
    Phase 4d (stock_groups.py migration): _load_master_stock_groups/
    _save_master_stock_groups now accept an optional store_id, mirroring the
    dumping_configs pattern (see test_dumping_configs_store_scoping.py).
    """

    def setUp(self):
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
        self.state_helpers._save_master_stock_groups(
            {"grp_A": make_group_dict("Group A")}, store_id="store-A"
        )
        groups = self.state_helpers._load_master_stock_groups(store_id="store-A")
        self.assertIn("grp_A", groups)
        self.assertEqual(groups["grp_A"]["group_name"], "Group A")

    def test_scoped_load_does_not_see_other_stores_data(self):
        self.state_helpers._save_master_stock_groups(
            {"grp_A": make_group_dict("Group A")}, store_id="store-A"
        )
        groups_b = self.state_helpers._load_master_stock_groups(store_id="store-B")
        self.assertEqual(groups_b, {})

    def test_scoped_load_does_not_see_legacy_unscoped_rows(self):
        self.state_helpers._save_master_stock_groups({"grp_LEGACY": make_group_dict("Legacy Group")})
        scoped = self.state_helpers._load_master_stock_groups(store_id="store-A")
        self.assertEqual(scoped, {})

    def test_unscoped_legacy_load_still_sees_everything(self):
        self.state_helpers._save_master_stock_groups({"grp_A": make_group_dict("A")}, store_id="store-A")
        self.state_helpers._save_master_stock_groups({"grp_B": make_group_dict("B")}, store_id="store-B")
        self.state_helpers._save_master_stock_groups({"grp_LEGACY": make_group_dict("Legacy")})
        legacy_view = self.state_helpers._load_master_stock_groups()
        self.assertEqual(set(legacy_view.keys()), {"grp_A", "grp_B", "grp_LEGACY"})

    def test_scoped_save_tags_a_previously_legacy_row(self):
        self.state_helpers._save_master_stock_groups({"grp_X": make_group_dict("X", shared_stock=1)})
        self.assertEqual(self.state_helpers._load_master_stock_groups(store_id="store-A"), {})

        self.state_helpers._save_master_stock_groups(
            {"grp_X": make_group_dict("X", shared_stock=2)}, store_id="store-A"
        )
        scoped = self.state_helpers._load_master_stock_groups(store_id="store-A")
        self.assertEqual(scoped["grp_X"]["shared_stock"], 2)

    def test_no_db_session_scoped_load_returns_empty_not_shared_file(self):
        os.makedirs("states", exist_ok=True)
        with open("states/master_stock_groups.json", "w", encoding="utf-8") as f:
            import json
            json.dump({"SOMEONE-ELSES-GROUP": make_group_dict("Leak")}, f)

        with patch.object(self.state_helpers, "_get_sync_db_session", return_value=None):
            scoped = self.state_helpers._load_master_stock_groups(store_id="store-A")
            self.assertEqual(scoped, {})

            legacy = self.state_helpers._load_master_stock_groups()
            self.assertIn("SOMEONE-ELSES-GROUP", legacy)


if __name__ == "__main__":
    unittest.main()
