import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app


class TestAnalyticsChatbotPrintBatcherNowRequireAuth(unittest.TestCase):
    """
    Phase 4d: analytics.py, chatbot.py, print_batcher.py had NO auth at all
    before this — anyone reaching the API could hit them. They're now wired
    to get_store_context, same as the money-critical routers. These tests
    confirm: (1) real legacy-session traffic (the only traffic that exists
    today) is unaffected, and (2) the new requirement actually holds — no
    session means 401, not a silent pass-through.
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

    def test_analytics_widgets_works_with_legacy_session(self):
        resp = self.client.get("/api/analytics/widgets")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_analytics_widgets_401_without_session(self):
        os.remove("states/session.json")
        resp = self.client.get("/api/analytics/widgets")
        self.assertEqual(resp.status_code, 401)

    def test_chatbot_config_works_with_legacy_session(self):
        resp = self.client.get("/api/chatbot/config")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_chatbot_config_401_without_session(self):
        os.remove("states/session.json")
        resp = self.client.get("/api/chatbot/config")
        self.assertEqual(resp.status_code, 401)

    def test_print_batcher_history_stats_works_with_legacy_session(self):
        resp = self.client.get("/api/print-batcher/history-stats")
        self.assertEqual(resp.status_code, 200)

    def test_print_batcher_history_stats_401_without_session(self):
        os.remove("states/session.json")
        resp = self.client.get("/api/print-batcher/history-stats")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
