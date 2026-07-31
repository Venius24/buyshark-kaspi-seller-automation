import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


def make_product(sku, price=1000):
    return {"sku": sku, "name": f"Товар {sku}", "price_num": price, "stock": 5}


class ProductsStoreScopingTest(unittest.TestCase):
    """
    Слайс 1: _load_products/_save_products теперь читают/пишут per-store
    (StoreProductDB), а не общий states/products.json. Эти тесты — доказательство,
    что каталог одного магазина не утекает в другой (ровно тот баг, из-за
    которого разные пользователи видели одну и ту же товарку).
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "products_scoping_test.db")
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
        self.state_helpers._save_products([make_product("SKU-A")], store_id="store-A")
        products = self.state_helpers._load_products(store_id="store-A")
        self.assertEqual([p["sku"] for p in products], ["SKU-A"])

    def test_scoped_load_does_not_see_other_stores_catalog(self):
        self.state_helpers._save_products([make_product("SKU-A")], store_id="store-A")
        products_b = self.state_helpers._load_products(store_id="store-B")
        self.assertEqual(products_b, [])

    def test_two_stores_keep_independent_catalogs(self):
        self.state_helpers._save_products([make_product("SKU-A", 100)], store_id="store-A")
        self.state_helpers._save_products([make_product("SKU-B", 200)], store_id="store-B")

        catalog_a = self.state_helpers._load_products(store_id="store-A")
        catalog_b = self.state_helpers._load_products(store_id="store-B")

        self.assertEqual([p["sku"] for p in catalog_a], ["SKU-A"])
        self.assertEqual([p["sku"] for p in catalog_b], ["SKU-B"])

    def test_saving_store_a_does_not_mutate_store_b_after_both_exist(self):
        self.state_helpers._save_products([make_product("SKU-A", 100)], store_id="store-A")
        self.state_helpers._save_products([make_product("SKU-B", 200)], store_id="store-B")

        # Re-save store A with an updated catalog — store B must stay untouched.
        self.state_helpers._save_products([make_product("SKU-A", 150), make_product("SKU-A2", 300)], store_id="store-A")

        catalog_b = self.state_helpers._load_products(store_id="store-B")
        self.assertEqual([p["sku"] for p in catalog_b], ["SKU-B"])

    def test_scoped_load_does_not_see_legacy_shared_file(self):
        os.makedirs("states", exist_ok=True)
        with open("states/products.json", "w", encoding="utf-8") as f:
            json.dump([make_product("SOMEONE-ELSES-SKU")], f)

        scoped = self.state_helpers._load_products(store_id="store-A")
        self.assertEqual(scoped, [])

    def test_save_call_requires_store_id(self):
        with self.assertRaises(TypeError):
            self.state_helpers._save_products([make_product("SKU-A")])

    def test_load_call_requires_store_id(self):
        with self.assertRaises(TypeError):
            self.state_helpers._load_products()

    def test_no_db_session_scoped_load_returns_empty(self):
        with patch.object(self.state_helpers, "_get_sync_db_session", return_value=None):
            self.assertEqual(self.state_helpers._load_products(store_id="store-A"), [])


if __name__ == "__main__":
    unittest.main()
