import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class ProductOverridesStoreScopingTest(unittest.TestCase):
    """
    Phase 4d (products.py migration): _load_overrides/_save_override/
    _apply_overrides now accept an optional store_id, mirroring the
    dumping_configs pattern (see test_dumping_configs_store_scoping.py).
    Evidence that scoped reads never leak another store's (or legacy,
    unscoped) override data.
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
        self.state_helpers._save_override("SKU-A", 100, 5, store_id="store-A")
        overrides = self.state_helpers._load_overrides(store_id="store-A")
        self.assertIn("SKU-A", overrides)
        self.assertEqual(overrides["SKU-A"]["price_num"], 100)

    def test_scoped_load_does_not_see_other_stores_data(self):
        self.state_helpers._save_override("SKU-A", 100, 5, store_id="store-A")
        overrides_b = self.state_helpers._load_overrides(store_id="store-B")
        self.assertEqual(overrides_b, {})

    def test_scoped_load_does_not_see_legacy_unscoped_rows(self):
        self.state_helpers._save_override("SKU-LEGACY", 50, 3)  # legacy, no store_id
        scoped = self.state_helpers._load_overrides(store_id="store-A")
        self.assertEqual(scoped, {})

    def test_unscoped_legacy_load_still_sees_every_scoped_row(self):
        """An unfiltered (store_id=None) load still returns every store's
        rows unfiltered — that mixing-view behavior is unchanged by Слайс 2."""
        self.state_helpers._save_override("SKU-A", 100, 5, store_id="store-A")
        self.state_helpers._save_override("SKU-B", 200, 6, store_id="store-B")
        legacy_view = self.state_helpers._load_overrides()
        self.assertEqual(set(legacy_view.keys()), {"SKU-A", "SKU-B"})

    def test_legacy_unscoped_save_no_longer_persists_to_db(self):
        """Слайс 2: store_id is now part of the primary key and NOT NULL —
        a legacy (store_id=None) save can no longer create a row at all.
        The DB write fails and is caught/logged/rolled back; it must not
        raise or corrupt other stores' data."""
        self.state_helpers._save_override("SKU-A", 100, 5, store_id="store-A")
        self.state_helpers._save_override("SKU-LEGACY", 300, 7)  # must not raise

        self.assertEqual(self.state_helpers._load_overrides(store_id="store-A")["SKU-A"]["price_num"], 100)
        self.assertNotIn("SKU-LEGACY", self.state_helpers._load_overrides())

    def test_apply_overrides_is_store_scoped(self):
        self.state_helpers._save_override("SKU-A", 555, 9, store_id="store-A")
        products = [{"sku": "SKU-A", "name": "Widget"}]

        scoped_result = self.state_helpers._apply_overrides(list(products), store_id="store-B")
        self.assertNotEqual(scoped_result[0].get("price_num"), 555)

        scoped_result_a = self.state_helpers._apply_overrides(list(products), store_id="store-A")
        self.assertEqual(scoped_result_a[0]["price_num"], 555)

    def test_no_db_session_scoped_load_returns_empty_not_shared_file(self):
        os.makedirs("states", exist_ok=True)
        with open("states/products_overrides.json", "w", encoding="utf-8") as f:
            import json
            json.dump({"SOMEONE-ELSES-SKU": {"price_num": 999, "stock": 1, "brand": None, "status": "Активен"}}, f)

        with patch.object(self.state_helpers, "_get_sync_db_session", return_value=None):
            scoped = self.state_helpers._load_overrides(store_id="store-A")
            self.assertEqual(scoped, {})

            legacy = self.state_helpers._load_overrides()
            self.assertIn("SOMEONE-ELSES-SKU", legacy)


if __name__ == "__main__":
    unittest.main()
