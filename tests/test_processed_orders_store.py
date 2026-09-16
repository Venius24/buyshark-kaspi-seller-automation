"""Отметки обработанных заказов: дельта-запись, точечное чтение, ретеншн.

Ради этих трёх свойств всё и затевалось. На проде (Бархан, 16.09) в таблице
лежало 10 343 отметки 'packaging' и 10 265 'stock', а код:

  * писал ВЕСЬ словарь отметок целиком, по одному session.get() на ключ —
    py-spy показал 68 секунд из 70 в этой функции, и всё это время держался
    store-лок, за которым стояли правки Мастер-Групп из кабинета;
  * читал ВСЮ таблицу, чтобы проверить один ключ брони (~2с на позицию заказа);
  * не удалял ничего никогда.
"""

import datetime
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

STORE = "store-A"


class ProcessedOrdersStoreTest(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 — регистрирует ORM-классы на Base
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "processed_orders_test.db")
        self._engine = create_engine(
            f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.sh = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._session_patch.start()

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _save(self, mapping, kind="packaging"):
        self.sh._save_processed_orders_by_kind(mapping, STORE, kind)

    def _load(self, kind="packaging", keys=None):
        return self.sh._load_processed_orders_by_kind(STORE, kind, keys=keys)

    # --- дельта-запись ------------------------------------------------------

    def test_save_inserts_new_keys(self):
        self._save({"a": {"units": 1}, "b": {"units": 2}})
        self.assertEqual(self._load(), {"a": {"units": 1}, "b": {"units": 2}})

    def test_save_updates_existing_key_in_place(self):
        self._save({"a": {"units": 1}})
        self._save({"a": {"units": 9}})
        self.assertEqual(self._load()["a"], {"units": 9})

    def test_save_does_not_touch_keys_it_was_not_given(self):
        """Главное свойство: вызывающий передаёт дельту, остальное цело."""
        self._save({"a": {"units": 1}, "b": {"units": 2}})
        self._save({"b": {"units": 5}})
        self.assertEqual(self._load(), {"a": {"units": 1}, "b": {"units": 5}})

    def test_empty_delta_is_a_no_op(self):
        self._save({"a": {"units": 1}})
        self._save({})
        self.assertEqual(self._load(), {"a": {"units": 1}})

    def test_kinds_stay_independent(self):
        self._save({"a": {"units": 1}}, kind="packaging")
        self._save({"a": {"claimed_by": "x"}}, kind="stock")
        self.assertEqual(self._load(kind="packaging")["a"], {"units": 1})
        self.assertEqual(self._load(kind="stock")["a"], {"claimed_by": "x"})

    def test_batch_larger_than_one_chunk_round_trips(self):
        """Пачки по 500 — проверяем, что склейка чанков ничего не теряет."""
        big = {f"key-{i}": {"units": i} for i in range(1100)}
        self._save(big)
        loaded = self._load()
        self.assertEqual(len(loaded), 1100)
        self.assertEqual(loaded["key-1099"], {"units": 1099})

    # --- точечное чтение ----------------------------------------------------

    def test_load_with_keys_returns_only_requested(self):
        self._save({"a": {"units": 1}, "b": {"units": 2}, "c": {"units": 3}})
        self.assertEqual(set(self._load(keys=["a", "c"])), {"a", "c"})

    def test_load_with_keys_ignores_missing_ones(self):
        self._save({"a": {"units": 1}})
        self.assertEqual(self._load(keys=["a", "nope"]), {"a": {"units": 1}})

    def test_load_with_empty_keys_hits_nothing(self):
        """Пустой список — это «ничего не спрашивали», а НЕ «отдай всё»."""
        self._save({"a": {"units": 1}})
        self.assertEqual(self._load(keys=[]), {})

    def test_load_without_keys_still_returns_everything(self):
        self._save({"a": {"units": 1}, "b": {"units": 2}})
        self.assertEqual(set(self._load()), {"a", "b"})

    # --- ретеншн ------------------------------------------------------------

    def _stamp(self, days_ago: int) -> str:
        return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    def test_prune_removes_entries_past_retention(self):
        self._save({"old": {"deducted_at": self._stamp(60)}})
        removed = self.sh._prune_processed_orders(STORE, "packaging")
        self.assertEqual(removed, 1)
        self.assertEqual(self._load(), {})

    def test_prune_keeps_entries_inside_retention(self):
        self._save({"fresh": {"deducted_at": self._stamp(3)}})
        self.assertEqual(self.sh._prune_processed_orders(STORE, "packaging"), 0)
        self.assertIn("fresh", self._load())

    def test_prune_keeps_anything_it_cannot_date(self):
        """Молча потерять бронь хуже, чем хранить лишнюю строку."""
        self._save({"undated": {"units": 1}, "garbled": {"deducted_at": "когда-то"}})
        self.assertEqual(self.sh._prune_processed_orders(STORE, "packaging"), 0)
        self.assertEqual(set(self._load()), {"undated", "garbled"})

    def test_prune_reads_isoformat_stamps_too(self):
        """'stock'-брони пишутся через .isoformat(), 'packaging' — strftime."""
        old = (datetime.datetime.now() - datetime.timedelta(days=60)).isoformat()
        self._save({"old": {"claimed_at": old}}, kind="stock")
        self.assertEqual(self.sh._prune_processed_orders(STORE, "stock"), 1)

    def test_prune_is_scoped_to_its_own_kind(self):
        self._save({"old": {"deducted_at": self._stamp(60)}}, kind="packaging")
        self._save({"old": {"claimed_at": self._stamp(60)}}, kind="stock")
        self.sh._prune_processed_orders(STORE, "packaging")
        self.assertEqual(self._load(kind="packaging"), {})
        self.assertIn("old", self._load(kind="stock"))

    def test_prune_is_scoped_to_its_own_store(self):
        self._save({"old": {"deducted_at": self._stamp(60)}})
        self.sh._save_processed_orders_by_kind(
            {"old": {"deducted_at": self._stamp(60)}}, "store-B", "packaging"
        )
        self.sh._prune_processed_orders(STORE, "packaging")
        self.assertIn(
            "old", self.sh._load_processed_orders_by_kind("store-B", "packaging")
        )

    def test_retention_window_cannot_drop_below_the_sweep_window(self):
        """Удалить отметку по заказу, который ещё в выборке — списать дважды."""
        from backend.services.stock_sync_worker import FULL_SWEEP_WINDOW_DAYS
        self.assertGreater(self.sh.PROCESSED_ORDERS_RETENTION_DAYS, FULL_SWEEP_WINDOW_DAYS)


if __name__ == "__main__":
    unittest.main()
