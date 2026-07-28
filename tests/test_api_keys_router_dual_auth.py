import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app


class TestApiKeysRouterLegacyPath(unittest.TestCase):
    """
    End-to-end check that migrating api_keys.py to the dual-auth dependency
    preserves EXACTLY today's behavior for real production traffic (no JWT
    sent at all). api_keys.json/webhook_config.json have no store_id
    column (Phase 4a didn't cover them), so only the auth resolution
    changed here — this test proves that swap alone didn't break anything.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)
        with open("states/session.json", "w", encoding="utf-8") as f:
            json.dump({"token": "test_token_pilot", "merchant_id": "12345678"}, f)
        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_get_keys_with_no_auth_header_uses_legacy_session(self):
        resp = self.client.get("/api/integrations/keys")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_create_and_revoke_key_round_trip(self):
        create_resp = self.client.post("/api/integrations/keys", json={"name": "1C Integration"})
        self.assertEqual(create_resp.status_code, 200)
        key_id = create_resp.json()["key_id"]

        list_resp = self.client.get("/api/integrations/keys")
        self.assertEqual(list_resp.json()["count"], 1)

        revoke_resp = self.client.delete(f"/api/integrations/keys/{key_id}")
        self.assertEqual(revoke_resp.status_code, 200)

        list_resp2 = self.client.get("/api/integrations/keys")
        self.assertEqual(list_resp2.json()["count"], 0)

    def test_webhook_config_round_trip(self):
        save_resp = self.client.post("/api/integrations/webhook", json={
            "url": "https://example.com/hook", "secret": "s3cr3t123", "enabled": True,
        })
        self.assertEqual(save_resp.status_code, 200)

        get_resp = self.client.get("/api/integrations/webhook")
        self.assertEqual(get_resp.json()["config"]["url"], "https://example.com/hook")

    def test_missing_legacy_session_returns_401_same_as_before(self):
        os.remove("states/session.json")
        resp = self.client.get("/api/integrations/keys")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
