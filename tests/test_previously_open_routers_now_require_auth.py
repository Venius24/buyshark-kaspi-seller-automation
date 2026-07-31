import os
import sys
import unittest

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app


class TestAnalyticsChatbotPrintBatcherRequireAuth(unittest.TestCase):
    """
    analytics.py, chatbot.py, print_batcher.py had NO auth at all before
    Phase 4d — anyone reaching the API could hit them. They're now wired to
    get_store_context, same as the money-critical routers. Слайс 7 removed
    the legacy shared-session fallback entirely, so a request without a
    valid JWT+store now always gets 401 — not a silent pass-through, and not
    a fallback to some other tenant's session either.
    """

    def setUp(self):
        self.client = TestClient(app)

    def test_analytics_widgets_401_without_auth(self):
        resp = self.client.get("/api/analytics/widgets")
        self.assertEqual(resp.status_code, 401)

    def test_chatbot_config_401_without_auth(self):
        resp = self.client.get("/api/chatbot/config")
        self.assertEqual(resp.status_code, 401)

    def test_print_batcher_history_stats_401_without_auth(self):
        resp = self.client.get("/api/print-batcher/history-stats")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
