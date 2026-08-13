import os
import sys
import shutil
import tempfile
import unittest
import datetime

sys.path.append(os.getcwd())

from backend import state_helpers
# Модели импортируем явно: _get_sync_db_session зовёт create_all ДО того, как
# ленивый импорт внутри _save_override зарегистрирует таблицу в метаданных, и
# на первом же обращении в чистом tmp-каталоге ловится "no such table".
from backend.db import models as _db_models  # noqa: F401


class TestOverrideMatchingAndFreshness(unittest.TestCase):
    """Регрессия 2026-08-13: каталог вечно показывал позавчерашнюю локальную цену.

    Оверрайды пишутся по двум разным ключам — демпер по merchantSku,
    ручная правка цены из панели по Kaspi sku, — а _apply_overrides матчил
    только по sku. В результате на свежий каталог из Kaspi ложился протухший
    оверрайд по masterSku (на проде: 605 ₸ от 12 августа поверх реальных
    595 ₸), демпер каждый цикл заново «исправлял» несуществующее отклонение,
    а offer_sync гасил запись вхолостую, сверившись с Kaspi. 90-100 фантомных
    изменений за цикл на ~100 SKU.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)
        self.store = "store-1"

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _product(self):
        return {
            "sku": "111881073", "merchantSku": "T219-0005-1",
            "name": "Сумка 25082024 3 л черный", "price": "595 ₸",
            "price_num": 595, "stock": 492, "status": "Активен",
        }

    def test_stale_override_does_not_shadow_fresh_kaspi_price(self):
        state_helpers._save_override("111881073", 605, 492, store_id=self.store)
        fetched_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=1)

        out = state_helpers._apply_overrides(
            [self._product()], store_id=self.store, fresh_from_kaspi_at=fetched_at
        )
        self.assertEqual(out[0]["price_num"], 595)

    def test_fresh_manual_edit_survives_sync(self):
        fetched_at = datetime.datetime.utcnow()
        # Правка руками во время выкачки — она новее отсечки, обязана дожить.
        state_helpers._save_override("111881073", 777, 492, store_id=self.store)

        out = state_helpers._apply_overrides(
            [self._product()], store_id=self.store, fresh_from_kaspi_at=fetched_at
        )
        self.assertEqual(out[0]["price_num"], 777)

    def test_merchant_sku_override_is_applied_without_freshness_barrier(self):
        state_helpers._save_override("T219-0005-1", 595, 492, store_id=self.store)
        p = self._product()
        p["price_num"], p["price"] = 605, "605 ₸"

        out = state_helpers._apply_overrides([p], store_id=self.store)
        self.assertEqual(out[0]["price_num"], 595)

    def test_newer_key_wins_when_both_keys_exist(self):
        state_helpers._save_override("T219-0005-1", 595, 492, store_id=self.store)
        state_helpers._save_override("111881073", 640, 492, store_id=self.store)

        out = state_helpers._apply_overrides([self._product()], store_id=self.store)
        self.assertEqual(out[0]["price_num"], 640)

        state_helpers._save_override("T219-0005-1", 588, 492, store_id=self.store)
        out = state_helpers._apply_overrides([self._product()], store_id=self.store)
        self.assertEqual(out[0]["price_num"], 588)


if __name__ == "__main__":
    unittest.main()
