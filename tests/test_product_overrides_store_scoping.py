import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class ProductOverridesStoreScopingTest(unittest.TestCase):
    """
    _load_overrides/_save_override/_apply_overrides require store_id (Слайс 7
    — legacy unscoped/shared-file path removed entirely). Evidence that
    scoped reads never leak another store's override data.
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

    def test_two_stores_keep_independent_overrides_for_the_same_sku(self):
        self.state_helpers._save_override("SKU-X", 100, 5, store_id="store-A")
        self.state_helpers._save_override("SKU-X", 200, 6, store_id="store-B")

        self.assertEqual(self.state_helpers._load_overrides(store_id="store-A")["SKU-X"]["price_num"], 100)
        self.assertEqual(self.state_helpers._load_overrides(store_id="store-B")["SKU-X"]["price_num"], 200)

    def test_apply_overrides_is_store_scoped(self):
        self.state_helpers._save_override("SKU-A", 555, 9, store_id="store-A")
        products = [{"sku": "SKU-A", "name": "Widget"}]

        scoped_result = self.state_helpers._apply_overrides(list(products), store_id="store-B")
        self.assertNotEqual(scoped_result[0].get("price_num"), 555)

        scoped_result_a = self.state_helpers._apply_overrides(list(products), store_id="store-A")
        self.assertEqual(scoped_result_a[0]["price_num"], 555)

    def test_save_call_requires_store_id(self):
        with self.assertRaises(TypeError):
            self.state_helpers._save_override("SKU-A", 100, 5)

    def test_load_call_requires_store_id(self):
        with self.assertRaises(TypeError):
            self.state_helpers._load_overrides()

    def test_no_db_session_scoped_load_returns_empty(self):
        with patch.object(self.state_helpers, "_get_sync_db_session", return_value=None):
            self.assertEqual(self.state_helpers._load_overrides(store_id="store-A"), {})


if __name__ == "__main__":
    unittest.main()
