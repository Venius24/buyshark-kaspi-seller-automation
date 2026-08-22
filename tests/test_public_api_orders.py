import os
import sys
import shutil
import tempfile
import datetime
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app


class PublicApiOrdersTest(unittest.TestCase):
    """
    Слайс 3 (продолжение): GET /api/v1/public/orders — тот же store-scoping
    контракт, что и /api/v1/public/groups (см. test_public_api_store_scoping.py),
    плюс пагинация курсором ?since= и резолв sku -> external_code через
    Мастер-Группы.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "public_api_orders_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

        state_helpers._create_api_key("store-A", "key_a", "1C A", "hash_a", "bk_a...", "2026-08-23 00:00:00")
        state_helpers._create_api_key("store-B", "key_b", "1C B", "hash_b", "bk_b...", "2026-08-23 00:00:00")

        state_helpers._save_master_stock_groups(
            {
                "grp_A": {
                    "group_name": "Group A", "shared_stock": 10, "linked_skus": ["SKU-A1"],
                    "external_code": "1C-ART-A1", "is_active": True, "dumping_config": None,
                    "status": "synced", "created_at": "2026-08-23 00:00:00", "updated_at": "2026-08-23 00:00:00",
                    "last_synced_at": None, "last_sync_log": None, "sync_logs": [],
                }
            },
            store_id="store-A",
        )

        state_helpers._upsert_sales_orders(
            [
                {
                    "internalId": "ord-A-1", "code": "A1", "creationDateMs": 1755900000000,
                    "totalPrice": 5000, "status": "COMPLETED", "state": "ARCHIVE",
                    "entries": [{"sku": "SKU-A1", "merchantSku": "M-A1", "name": "Товар A1", "qty": 1, "totalPrice": 5000}],
                }
            ],
            "store-A",
        )
        state_helpers._upsert_sales_orders(
            [
                {
                    "internalId": "ord-B-1", "code": "B1", "creationDateMs": 1755900000000,
                    "totalPrice": 3000, "status": "COMPLETED", "state": "ARCHIVE",
                    "entries": [{"sku": "SKU-B1", "merchantSku": "M-B1", "name": "Товар B1", "qty": 1, "totalPrice": 3000}],
                }
            ],
            "store-B",
        )

        self.client = TestClient(app)

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_key_a_sees_only_its_own_orders(self):
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "hash_a"
            resp = self.client.get("/api/v1/public/orders", headers={"X-API-Key": "raw_a"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["order_internal_id"], "ord-A-1")

    def test_order_line_resolves_external_code_from_master_group(self):
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "hash_a"
            resp = self.client.get("/api/v1/public/orders", headers={"X-API-Key": "raw_a"})
        entry = resp.json()["data"][0]["entries"][0]
        self.assertEqual(entry["external_code"], "1C-ART-A1")

    def test_unmapped_sku_resolves_to_null_external_code(self):
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "hash_b"
            resp = self.client.get("/api/v1/public/orders", headers={"X-API-Key": "raw_b"})
        entry = resp.json()["data"][0]["entries"][0]
        self.assertIsNone(entry["external_code"])

    def test_since_cursor_excludes_already_seen_order(self):
        future = (datetime.datetime.utcfromtimestamp(1755900000000 / 1000.0) + datetime.timedelta(days=1)).isoformat()
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "hash_a"
            resp = self.client.get(f"/api/v1/public/orders?since={future}", headers={"X-API-Key": "raw_a"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"], [])

    def test_invalid_key_rejected(self):
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "no_such_hash"
            resp = self.client.get("/api/v1/public/orders", headers={"X-API-Key": "garbage"})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
