import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.db.models import Store
from backend.security.store_context import StoreContext, get_store_context


class TestAutoAssemblyRouter(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(Store(id="store-1", merchant_id="m_store1", name="TestShop"))
        session.commit()
        session.close()

        self.client = TestClient(app)
        app.dependency_overrides[get_store_context] = lambda: StoreContext(
            token="fake-token", merchant_id="12345678", store_id="store-1"
        )

    def tearDown(self):
        app.dependency_overrides.pop(get_store_context, None)
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_get_rules_returns_full_grid_with_defaults(self):
        resp = self.client.get("/api/auto-assembly/rules")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["success"])
        delivery_types = {r["delivery_type"] for r in data["rules"]}
        self.assertEqual(
            delivery_types,
            {"Express", "Postomat", "Самовывоз", "Today", "Tomorrow", "Kaspi Доставка"},
        )
        for r in data["rules"]:
            self.assertFalse(r["enabled"])

    def test_put_rule_rejects_unknown_delivery_type(self):
        resp = self.client.put("/api/auto-assembly/rules", json={
            "delivery_type": "Телепортация", "enabled": True,
        })
        self.assertEqual(resp.status_code, 400)

    def test_put_then_get_round_trips_all_fields(self):
        payload = {
            "delivery_type": "Express",
            "enabled": True,
            "dry_run": False,
            "stage_accept": True,
            "stage_assemble": True,
            "stage_waybill": False,
            "schedule": {"start": "09:00", "end": "19:00"},
            "max_order_price": 50000.0,
            "sku_blacklist": ["SKU-1"],
            "respect_stock_groups": False,
            "max_per_cycle": 10,
        }
        put_resp = self.client.put("/api/auto-assembly/rules", json=payload)
        self.assertEqual(put_resp.status_code, 200, put_resp.text)

        get_resp = self.client.get("/api/auto-assembly/rules")
        rules_by_type = {r["delivery_type"]: r for r in get_resp.json()["rules"]}
        saved = rules_by_type["Express"]
        self.assertTrue(saved["enabled"])
        self.assertFalse(saved["dry_run"])
        self.assertEqual(saved["schedule"], {"start": "09:00", "end": "19:00"})
        self.assertEqual(saved["sku_blacklist"], ["SKU-1"])
        self.assertEqual(saved["max_order_price"], 50000.0)
        self.assertEqual(saved["max_per_cycle"], 10)

    def test_put_rule_is_upsert_not_duplicate(self):
        payload = {"delivery_type": "Express", "enabled": True}
        self.client.put("/api/auto-assembly/rules", json=payload)
        self.client.put("/api/auto-assembly/rules", json={**payload, "enabled": False})

        get_resp = self.client.get("/api/auto-assembly/rules")
        express_rules = [r for r in get_resp.json()["rules"] if r["delivery_type"] == "Express"]
        self.assertEqual(len(express_rules), 1)
        self.assertFalse(express_rules[0]["enabled"])

    def test_rules_are_scoped_by_store(self):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(Store(id="store-2", merchant_id="m_store2", name="OtherShop"))
        session.commit()
        session.close()

        app.dependency_overrides[get_store_context] = lambda: StoreContext(
            token="fake-token-2", merchant_id="m_store2", store_id="store-2"
        )
        self.client.put("/api/auto-assembly/rules", json={"delivery_type": "Express", "enabled": True})

        app.dependency_overrides[get_store_context] = lambda: StoreContext(
            token="fake-token", merchant_id="12345678", store_id="store-1"
        )
        get_resp = self.client.get("/api/auto-assembly/rules")
        express = next(r for r in get_resp.json()["rules"] if r["delivery_type"] == "Express")
        self.assertFalse(express["enabled"])

    def test_get_log_empty_by_default(self):
        resp = self.client.get("/api/auto-assembly/log")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["entries"], [])

    def test_get_log_returns_entries_newest_first(self):
        from backend.services.auto_assembly_worker import _log
        _log("store-1", "100111", "accept", "DONE", "Заказ принят")
        _log("store-1", "100222", "accept", "DONE", "Заказ принят")

        resp = self.client.get("/api/auto-assembly/log")
        entries = resp.json()["entries"]
        self.assertEqual([e["order_code"] for e in entries], ["100222", "100111"])

    def test_get_spool_empty_by_default(self):
        resp = self.client.get("/api/auto-assembly/spool")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 0)

    def test_spool_download_404_when_nothing_spooled(self):
        resp = self.client.post("/api/auto-assembly/spool/download")
        self.assertEqual(resp.status_code, 404)

    def test_run_once_invokes_auto_assembly_cycle(self):
        with patch("backend.routers.auto_assembly.run_auto_assembly_cycle") as mock_cycle:
            async def _noop(*a, **kw):
                return None
            mock_cycle.side_effect = _noop
            resp = self.client.post("/api/auto-assembly/run-once")
        self.assertEqual(resp.status_code, 200, resp.text)
        mock_cycle.assert_called_once_with("fake-token", "store-1")

    def test_mark_printed_with_no_spool_returns_zero(self):
        resp = self.client.post("/api/auto-assembly/spool/mark-printed", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["marked_count"], 0)


if __name__ == "__main__":
    unittest.main()
