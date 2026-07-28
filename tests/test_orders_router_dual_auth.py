import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app


class TestOrdersRouterLegacyPath(unittest.TestCase):
    """
    End-to-end check that migrating orders.py to the dual-auth dependency
    preserves EXACTLY today's behavior for real production traffic (no JWT
    sent at all) — orders.py has no local store-scoped data, just a token
    swap, so this is the whole surface area that could have broken.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)
        with open("states/session.json", "w", encoding="utf-8") as f:
            # merchant_id must be a pure digit string so discover_merchant_id()
            # resolves it from the session file without a real network call.
            json.dump({"token": "test_token_pilot", "merchant_id": "12345678"}, f)
        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch("backend.kaspi_wrapper.KaspiAPI.get_cities")
    def test_get_cities_with_no_auth_header_uses_legacy_session(self, mock_cities):
        mock_cities.return_value = [{"id": "750000000", "name": "Алматы"}]
        resp = self.client.get("/api/cities")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        mock_cities.assert_called_once_with("test_token_pilot")

    def test_missing_legacy_session_returns_401_same_as_before(self):
        os.remove("states/session.json")
        resp = self.client.get("/api/cities")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
