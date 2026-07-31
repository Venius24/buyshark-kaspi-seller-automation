import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.security.store_context import StoreContext


def make_group_dict(group_name="Test Group", shared_stock=10, external_code=None):
    return {
        "group_name": group_name,
        "shared_stock": shared_stock,
        "linked_skus": ["SKU-1"],
        "external_code": external_code,
        "is_active": True,
        "dumping_config": None,
        "status": "synced",
        "created_at": "2026-07-31 00:00:00",
        "updated_at": "2026-07-31 00:00:00",
        "last_synced_at": None,
        "last_sync_log": None,
        "sync_logs": [],
    }


class PublicApiStoreScopingTest(unittest.TestCase):
    """
    Слайс 3: до этого X-API-Key не резолвил store_id вообще — любой валидный
    ключ 1С/ERP видел и мог менять Мастер-Группы остатков ЛЮБОГО магазина
    через /api/v1/public/*. Эти тесты — доказательство, что ключ магазина A
    не видит и не может изменить данные магазина B.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401 registers ORM classes on Base.metadata
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "public_api_scoping_test.db")
        self._sync_engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._sync_engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._sync_engine)
        )
        self._session_patch.start()

        # Two stores, each with their own API key and their own group.
        state_helpers._create_api_key("store-A", "key_a", "1C A", "hash_a", "bk_a...", "2026-07-31 00:00:00")
        state_helpers._create_api_key("store-B", "key_b", "1C B", "hash_b", "bk_b...", "2026-07-31 00:00:00")
        state_helpers._save_master_stock_groups(
            {"grp_A": make_group_dict("Group A", shared_stock=5, external_code="1C-A")}, store_id="store-A"
        )
        state_helpers._save_master_stock_groups(
            {"grp_B": make_group_dict("Group B", shared_stock=7, external_code="1C-B")}, store_id="store-B"
        )

        self.client = TestClient(app)

    def tearDown(self):
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_key_a_sees_only_its_own_group(self):
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "hash_a"
            resp = self.client.get("/api/v1/public/groups", headers={"X-API-Key": "raw_a"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["group_id"], "grp_A")

    def test_key_b_sees_only_its_own_group(self):
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "hash_b"
            resp = self.client.get("/api/v1/public/groups", headers={"X-API-Key": "raw_b"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["group_id"], "grp_B")

    def test_invalid_key_rejected(self):
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "no_such_hash"
            resp = self.client.get("/api/v1/public/groups", headers={"X-API-Key": "garbage"})
        self.assertEqual(resp.status_code, 401)

    @patch("backend.routers.public_api.groups.get_store_by_id", new_callable=AsyncMock)
    def test_key_a_cannot_update_store_b_group_by_external_code(self, mock_get_store):
        mock_get_store.return_value = StoreContext(token="tok-a", merchant_id="m_a", store_id="store-A")
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "hash_a"
            resp = self.client.post(
                "/api/v1/public/stocks/update",
                json={"external_code": "1C-B", "new_stock": 99},
                headers={"X-API-Key": "raw_a"},
            )
        self.assertEqual(resp.status_code, 404)

    @patch("backend.routers.public_api.groups.get_store_by_id", new_callable=AsyncMock)
    def test_key_a_updates_its_own_group_only(self, mock_get_store):
        mock_get_store.return_value = StoreContext(token="tok-a", merchant_id="m_a", store_id="store-A")
        with patch("backend.routers.public_api.auth.hashlib.sha256") as mock_sha256:
            mock_sha256.return_value.hexdigest.return_value = "hash_a"
            resp = self.client.post(
                "/api/v1/public/stocks/update",
                json={"external_code": "1C-A", "new_stock": 42},
                headers={"X-API-Key": "raw_a"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["updated_stock"], 42)

        groups_b = self.state_helpers._load_master_stock_groups(store_id="store-B")
        self.assertEqual(groups_b["grp_B"]["shared_stock"], 7)  # untouched


if __name__ == "__main__":
    unittest.main()
