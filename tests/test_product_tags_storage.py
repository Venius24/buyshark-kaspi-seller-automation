"""
Хранение меток товаров: помагазинность и замена набора целиком.

Метка живёт на артикуле, а не на Мастер-Группе, поэтому единственная защита
от протечки между магазинами — ведущий store_id в ключе. Здесь это и
проверяется, вместе с тем, что PUT заменяет набор, а не дописывает к нему.
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


class ProductTagsStorageTest(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 регистрирует ORM-классы на Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "tags_test.db")
        self._engine = create_engine(
            f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.sh = state_helpers
        self._patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_save_then_load(self):
        self.sh._save_product_tags("store-A", "X179-0013", ["Хрупкое", "Проверить"])
        self.assertEqual(
            self.sh._load_product_tags("store-A"),
            {"X179-0013": ["Проверить", "Хрупкое"]},
        )

    def test_tags_do_not_leak_between_stores(self):
        self.sh._save_product_tags("store-A", "X179-0013", ["Хрупкое"])
        self.assertEqual(self.sh._load_product_tags("store-B"), {})

    def test_put_replaces_the_whole_set(self):
        self.sh._save_product_tags("store-A", "X179-0013", ["Хрупкое", "Проверить"])
        self.sh._save_product_tags("store-A", "X179-0013", ["Хрупкое"])
        self.assertEqual(self.sh._load_product_tags("store-A"), {"X179-0013": ["Хрупкое"]})

    def test_empty_list_clears_tags(self):
        self.sh._save_product_tags("store-A", "X179-0013", ["Хрупкое"])
        self.sh._save_product_tags("store-A", "X179-0013", [])
        self.assertEqual(self.sh._load_product_tags("store-A"), {})

    def test_duplicates_and_blanks_are_dropped(self):
        saved = self.sh._save_product_tags(
            "store-A", "X179-0013", ["Хрупкое", "  ", "хрупкое", "", "Проверить"]
        )
        self.assertEqual(saved, ["Хрупкое", "Проверить"])

    def test_load_can_be_narrowed_to_given_skus(self):
        self.sh._save_product_tags("store-A", "X179-0013", ["Хрупкое"])
        self.sh._save_product_tags("store-A", "H477-0024", ["Объёмное"])
        self.assertEqual(
            self.sh._load_product_tags("store-A", skus=["H477-0024"]),
            {"H477-0024": ["Объёмное"]},
        )

    def test_store_tag_list_counts_products(self):
        self.sh._save_product_tags("store-A", "X179-0013", ["Хрупкое"])
        self.sh._save_product_tags("store-A", "H477-0024", ["Хрупкое", "Объёмное"])
        self.assertEqual(
            self.sh._list_store_tags("store-A"),
            [{"tag": "Хрупкое", "products": 2}, {"tag": "Объёмное", "products": 1}],
        )


if __name__ == "__main__":
    unittest.main()
