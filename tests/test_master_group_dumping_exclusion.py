import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())


def make_group_dict(linked_skus, dumping_config=None, dumping_excluded_skus=None):
    return {
        "group_name": "Test Group",
        "shared_stock": 10,
        "linked_skus": linked_skus,
        "external_code": None,
        "is_active": True,
        "stop_qty": 5,
        "dumping_config": dumping_config,
        "dumping_excluded_skus": dumping_excluded_skus or [],
        "status": "synced",
        "created_at": "2026-08-12 00:00:00",
        "updated_at": "2026-08-12 00:00:00",
        "last_synced_at": None,
        "last_sync_log": None,
        "sync_logs": [],
    }


class MasterGroupDumpingExclusionPersistenceTest(unittest.TestCase):
    """dumping_excluded_skus roundtrips через _save_master_stock_groups/_load_master_stock_groups
    (Text-JSON колонка, добавленная миграцией 030)."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "excl_test.db")
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

    def test_roundtrip_excluded_skus(self):
        group = make_group_dict(["SKU-1", "SKU-2"], dumping_excluded_skus=["SKU-2"])
        self.state_helpers._save_master_stock_groups({"grp_A": group}, store_id="store-A")
        loaded = self.state_helpers._load_master_stock_groups(store_id="store-A")
        self.assertEqual(loaded["grp_A"]["dumping_excluded_skus"], ["SKU-2"])

    def test_default_empty_list_when_never_set(self):
        group = make_group_dict(["SKU-1"])
        self.state_helpers._save_master_stock_groups({"grp_A": group}, store_id="store-A")
        loaded = self.state_helpers._load_master_stock_groups(store_id="store-A")
        self.assertEqual(loaded["grp_A"]["dumping_excluded_skus"], [])


class MasterGroupDumpingExclusionEnrichmentTest(unittest.IsolatedAsyncioTestCase):
    """Товар из dumping_excluded_skus не должен получать групповой
    dumping_config в effective_configs (backend/dumper/__init__.py), но
    сохранять свой individual dumping_config, если он был явно задан."""

    async def test_excluded_sku_does_not_get_group_dumping_config(self):
        from backend.dumper import run_price_dumping_job

        group_cfg = {"enabled": True, "min_price": 100, "max_price": 200, "mode": "TOP_1"}
        groups = {
            "grp_A": make_group_dict(
                ["SKU-1", "SKU-2"],
                dumping_config=group_cfg,
                dumping_excluded_skus=["SKU-2"],
            )
        }

        captured = {}

        async def fake_scan_market(products, effective_configs, **kwargs):
            captured["effective_configs"] = effective_configs
            return None  # прерываем цикл сразу после обогащения — сканирование не нужно для этого теста

        with patch("backend.state_helpers._load_master_stock_groups", return_value=groups), \
             patch("backend.dumper.discover_merchant_id", return_value="123456"), \
             patch("backend.dumper.RadarWorker.scan_market", side_effect=fake_scan_market):
            await run_price_dumping_job(
                token="fake-token",
                configs={},
                products=[],
                save_override_func=lambda *a, **k: None,
                store_id="store-A",
            )

        effective = captured["effective_configs"]
        self.assertIn("SKU-1", effective)
        self.assertNotIn("SKU-2", effective)

    async def test_excluded_sku_keeps_its_own_individual_config(self):
        from backend.dumper import run_price_dumping_job

        group_cfg = {"enabled": True, "min_price": 100, "max_price": 200, "mode": "TOP_1"}
        indiv_cfg = {"enabled": True, "min_price": 50, "max_price": 60, "sku": "SKU-2"}
        groups = {
            "grp_A": make_group_dict(
                ["SKU-1", "SKU-2"],
                dumping_config=group_cfg,
                dumping_excluded_skus=["SKU-2"],
            )
        }

        captured = {}

        async def fake_scan_market(products, effective_configs, **kwargs):
            captured["effective_configs"] = effective_configs
            return None

        with patch("backend.state_helpers._load_master_stock_groups", return_value=groups), \
             patch("backend.dumper.discover_merchant_id", return_value="123456"), \
             patch("backend.dumper.RadarWorker.scan_market", side_effect=fake_scan_market):
            await run_price_dumping_job(
                token="fake-token",
                configs={"SKU-2": indiv_cfg},
                products=[],
                save_override_func=lambda *a, **k: None,
                store_id="store-A",
            )

        effective = captured["effective_configs"]
        # Индивидуальный конфиг SKU-2 не тронут групповым исключением.
        self.assertEqual(effective["SKU-2"]["min_price"], 50)


class MasterGroupDumpingExclusionAliasTest(unittest.IsolatedAsyncioTestCase):
    """
    Регрессия (прод, 2026-08-12): один физический оффер лежит в linked_skus под
    ДВУМЯ идентификаторами — кодом мастер-карточки Kaspi ("102050852", это
    поле sku/kaspiSku в ассортименте) и нашим merchantSku ("247353364").
    Пользователь исключил брендовую карточку по первому, а демпинг продолжил
    работать по второму и записал ей новую цену.
    """

    async def _effective_configs(self, products, groups, configs=None):
        from backend.dumper import run_price_dumping_job

        captured = {}

        async def fake_scan_market(prods, effective_configs, **kwargs):
            captured["effective_configs"] = effective_configs
            return None

        with patch("backend.state_helpers._load_master_stock_groups", return_value=groups), \
             patch("backend.dumper.discover_merchant_id", return_value="123456"), \
             patch("backend.dumper.RadarWorker.scan_market", side_effect=fake_scan_market):
            await run_price_dumping_job(
                token="fake-token",
                configs=configs or {},
                products=products,
                save_override_func=lambda *a, **k: None,
                store_id="store-A",
            )
        return captured["effective_configs"]

    def _products(self):
        return [
            {"sku": "102050852", "merchantSku": "247353364", "kaspiSku": "102050852",
             "name": "Термометр Barkhan D673-0001 белый"},
            {"sku": "173596408", "merchantSku": "D673-0001-2", "kaspiSku": "173596408",
             "name": "Термометр PROconnect"},
        ]

    async def test_exclusion_by_master_card_code_also_covers_the_merchant_sku(self):
        groups = {"grp_A": make_group_dict(
            ["102050852", "247353364", "173596408"],
            dumping_config={"enabled": True, "min_price": 100, "max_price": 200},
            dumping_excluded_skus=["102050852"],
        )}
        effective = await self._effective_configs(self._products(), groups)
        self.assertNotIn("102050852", effective)
        self.assertNotIn("247353364", effective)
        # Соседний товар группы не задет.
        self.assertIn("173596408", effective)

    async def test_exclusion_by_merchant_sku_also_covers_the_master_card_code(self):
        groups = {"grp_A": make_group_dict(
            ["102050852", "247353364", "173596408"],
            dumping_config={"enabled": True, "min_price": 100, "max_price": 200},
            dumping_excluded_skus=["247353364"],
        )}
        effective = await self._effective_configs(self._products(), groups)
        self.assertNotIn("102050852", effective)
        self.assertNotIn("247353364", effective)
        self.assertIn("173596408", effective)

    async def test_unknown_sku_falls_back_to_literal_comparison(self):
        # Товара нет в ассортименте (например, снят с продажи) — сравниваем
        # как раньше, по строке, а не теряем исключение.
        groups = {"grp_A": make_group_dict(
            ["GHOST-1", "173596408"],
            dumping_config={"enabled": True, "min_price": 100, "max_price": 200},
            dumping_excluded_skus=["GHOST-1"],
        )}
        effective = await self._effective_configs(self._products(), groups)
        self.assertNotIn("GHOST-1", effective)
        self.assertIn("173596408", effective)


if __name__ == "__main__":
    unittest.main()
