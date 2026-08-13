"""
Правка и удаление Мастер-Группы.

Два бага, о которых сообщил пользователь 2026-08-12:

1. Настройку демпинга у группы нельзя было убрать. Фронт при выключении
   шлёт dumping_config: null, а роутер отбрасывал поле по условию
   `req.dumping_config is not None` — "поле не прислали" и "прислали пустым"
   по значению None неразличимы. Форма закрывалась с "успешно обновлена", а
   группа продолжала демпинговаться со старым конфигом.

2. Группу нельзя было удалить. Роутер выбрасывал её из словаря и звал
   _save_master_stock_groups, который делает ТОЛЬКО upsert по ключам
   словаря и ничего не удаляет — строка оставалась в БД, и группа
   возвращалась при следующей загрузке списка.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.append(os.getcwd())

from backend.main import app
from backend.security.store_context import StoreContext, get_store_context

STORE = "store-test"


def make_group(group_id="grp_A", dumping_config=None, external_code=None):
    return {
        "group_id": group_id,
        "group_name": "ТЕРМУХИ",
        "shared_stock": 500,
        "linked_skus": ["T219-0005", "131523919"],
        "external_code": external_code,
        "is_active": True,
        "stop_qty": 5,
        "dumping_config": dumping_config,
        "dumping_excluded_skus": [],
        "status": "synced",
        "created_at": "2026-08-12 00:00:00",
        "updated_at": "2026-08-12 00:00:00",
        "last_synced_at": None,
        "last_sync_log": None,
        "sync_logs": [],
    }


ENABLED_CFG = {
    "enabled": True, "min_price": 500, "max_price": 900,
    "dump_step": 1.0, "mode": "TOP_1", "target_position": 1,
}


class StockGroupsRouterTestCase(unittest.TestCase):
    """Роутер поверх настоящей (временной) БД — иначе баг с upsert не виден."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp = tempfile.mkdtemp()

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        path = os.path.join(self._tmp, "groups.db").replace(os.sep, "/")
        self._engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._session_patch.start()

        # Веерная синхронизация ходит в Kaspi — в этих тестах она не нужна.
        self._sync_patch = patch("backend.routers.stock_groups.execute_broadcast_sync")
        self._sync_patch.start()
        # _expand_linked_skus читает кэш ассортимента; пустой список означает
        # "оставить SKU как есть", чего для этих тестов достаточно.
        self._products_patch = patch("backend.routers.stock_groups._load_products", return_value=[])
        self._products_patch.start()

        self.client = TestClient(app)
        app.dependency_overrides[get_store_context] = lambda: StoreContext(
            token="tok", merchant_id="12345678", store_id=STORE
        )

    def tearDown(self):
        app.dependency_overrides.pop(get_store_context, None)
        self._products_patch.stop()
        self._sync_patch.stop()
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _seed(self, group):
        self.state_helpers._save_master_stock_groups({group["group_id"]: group}, store_id=STORE)

    def _reload(self, group_id="grp_A"):
        return self.state_helpers._load_master_stock_groups(store_id=STORE).get(group_id)


class TestClearingDumpingConfig(StockGroupsRouterTestCase):
    def test_explicit_null_removes_the_group_dumping_config(self):
        self._seed(make_group(dumping_config=ENABLED_CFG))
        resp = self.client.put("/api/stock-groups/grp_A", json={
            "group_name": "ТЕРМУХИ",
            "shared_stock": 500,
            "stop_qty": 5,
            "linked_skus": ["T219-0005", "131523919"],
            "is_active": True,
            "dumping_config": None,
            "dumping_excluded_skus": [],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self._reload()["dumping_config"])

    def test_omitted_field_keeps_the_existing_config(self):
        # Частичное обновление (например, только остаток) не должно сносить
        # настройки демпинга — иначе лечение было бы хуже болезни.
        self._seed(make_group(dumping_config=ENABLED_CFG))
        resp = self.client.put("/api/stock-groups/grp_A", json={"shared_stock": 480})
        self.assertEqual(resp.status_code, 200)
        group = self._reload()
        self.assertEqual(group["shared_stock"], 480)
        self.assertIsNotNone(group["dumping_config"])
        self.assertTrue(group["dumping_config"]["enabled"])

    def test_config_can_be_set_and_then_replaced(self):
        self._seed(make_group(dumping_config=None))
        self.client.put("/api/stock-groups/grp_A", json={"dumping_config": ENABLED_CFG})
        self.assertEqual(self._reload()["dumping_config"]["max_price"], 900)
        self.client.put("/api/stock-groups/grp_A", json={
            "dumping_config": {**ENABLED_CFG, "max_price": 1200},
        })
        self.assertEqual(self._reload()["dumping_config"]["max_price"], 1200)

    def test_external_code_can_be_cleared_too(self):
        # Тот же класс бага: поле сбрасывается пустой строкой.
        self._seed(make_group(external_code="1C-42"))
        self.client.put("/api/stock-groups/grp_A", json={"external_code": ""})
        self.assertIsNone(self._reload()["external_code"])

    def test_cleared_config_stops_group_dumping_enrichment(self):
        # Смысл фикса не в поле БД, а в том, что демпинг действительно встаёт.
        import asyncio

        from backend.dumper import run_price_dumping_job

        self._seed(make_group(dumping_config=ENABLED_CFG))
        self.client.put("/api/stock-groups/grp_A", json={
            "linked_skus": ["T219-0005", "131523919"], "dumping_config": None,
        })

        captured = {}

        async def fake_scan(products, effective_configs, **kwargs):
            captured["cfg"] = effective_configs
            return None

        with patch("backend.dumper.discover_merchant_id", return_value="12345678"), \
             patch("backend.dumper.RadarWorker.scan_market", side_effect=fake_scan):
            asyncio.run(run_price_dumping_job(
                token="tok", configs={}, products=[],
                save_override_func=lambda *a, **k: None, store_id=STORE,
            ))
        self.assertEqual(captured["cfg"], {})


class TestDeletingGroup(StockGroupsRouterTestCase):
    def test_delete_actually_removes_the_row(self):
        self._seed(make_group())
        resp = self.client.delete("/api/stock-groups/grp_A")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        # Именно перезагрузка из БД: раньше группа возвращалась здесь.
        self.assertEqual(self.state_helpers._load_master_stock_groups(store_id=STORE), {})

    def test_delete_leaves_other_groups_untouched(self):
        self._seed(make_group("grp_A"))
        self._seed(make_group("grp_B"))
        self.client.delete("/api/stock-groups/grp_A")
        remaining = self.state_helpers._load_master_stock_groups(store_id=STORE)
        self.assertEqual(list(remaining), ["grp_B"])

    def test_delete_unknown_group_is_404(self):
        resp = self.client.delete("/api/stock-groups/grp_MISSING")
        self.assertEqual(resp.status_code, 404)

    def test_delete_cannot_reach_another_stores_group(self):
        self.state_helpers._save_master_stock_groups({"grp_X": make_group("grp_X")}, store_id="store-other")
        resp = self.client.delete("/api/stock-groups/grp_X")
        # Для этого магазина группы не существует — 404, и чужая строка цела.
        self.assertEqual(resp.status_code, 404)
        self.assertIn("grp_X", self.state_helpers._load_master_stock_groups(store_id="store-other"))


if __name__ == "__main__":
    unittest.main()
