"""
GET/POST /api/chats* — own chat UI backed by Kaspi's chat API (see
REAL_IMPORTANT/чаты_kaspi/KASPI_CHAT_API.md). Covers: no chat session yet
(distinct from the general MC session) surfaces a clear actionable error,
and the happy paths call chat_client with the resolved store's cookies.
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.security.auth import create_access_token
from backend.db.models import User, Store
from backend.security.crypto import encrypt_data


def make_scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    scalars_result = MagicMock()
    scalars_result.first.return_value = value
    result.scalars.return_value = scalars_result
    return result


class TestChatsRouter(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.access_tok = create_access_token({"sub": "user-1"})
        self.user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        self.store = Store(
            id="store-1", merchant_id="m_1", name="Shop",
            encrypted_api_key=encrypt_data("rest_token"),
        )

    def _auth_override(self):
        fake_db = AsyncMock()
        fake_db.execute.side_effect = [make_scalar_result(self.user), make_scalar_result(self.store)]

        async def fake_get_db_gen():
            yield fake_db

        from backend.db.database import get_db as real_get_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen

    def tearDown(self):
        from backend.db.database import get_db as real_get_db
        app.dependency_overrides.pop(real_get_db, None)

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_tok}"}

    @patch("backend.routers.chats.load_chat_cookies")
    def test_list_chats_without_chat_session_returns_actionable_400(self, mock_load_cookies):
        mock_load_cookies.return_value = {}
        self._auth_override()

        resp = self.client.get("/api/chats", headers=self._headers())

        self.assertEqual(resp.status_code, 400)
        self.assertIn("SMS", resp.json()["detail"])

    @patch("backend.routers.chats.discover_merchant_id")
    @patch("backend.chatbot.chat_client.http_session")
    @patch("backend.routers.chats.load_chat_cookies")
    def test_list_chats_with_session_returns_groups(self, mock_load_cookies, mock_http, mock_discover):
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_discover.return_value = "20186986"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"groups": [{"id": "g1", "title": "Товар"}]}}
        mock_http.post.return_value = mock_resp
        self._auth_override()

        resp = self.client.get("/api/chats", headers=self._headers())

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["chats"]), 1)
        self.assertEqual(data["chats"][0]["id"], "g1")
        self.assertEqual(data["chats"][0]["title"], "Товар")
        self.assertFalse(data["chats"][0]["is_human_override"])

    @patch("backend.routers.chats.discover_merchant_id")
    @patch("backend.chatbot.chat_client.http_session")
    @patch("backend.routers.chats.load_chat_cookies")
    def test_send_message_success(self, mock_load_cookies, mock_http, mock_discover):
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_discover.return_value = "20186986"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"StatusCode": 0, "data": {"id": "msg-1", "text": "hi"}}
        mock_http.post.return_value = mock_resp
        self._auth_override()

        resp = self.client.post("/api/chats/g1/messages", json={"text": "hi"}, headers=self._headers())

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_send_empty_message_rejected(self):
        self._auth_override()
        with patch("backend.routers.chats.load_chat_cookies", return_value={"mc-session": "s", "mc-sid": "sid"}):
            resp = self.client.post("/api/chats/g1/messages", json={"text": "   "}, headers=self._headers())
        self.assertEqual(resp.status_code, 400)

    @patch("backend.routers.chats.discover_merchant_id")
    @patch("backend.chatbot.chat_client.http_session")
    @patch("backend.routers.chats.load_chat_cookies")
    def test_send_message_kaspi_rejection_surfaces_502(self, mock_load_cookies, mock_http, mock_discover):
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_discover.return_value = "20186986"
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_http.post.return_value = mock_resp
        self._auth_override()

        resp = self.client.post("/api/chats/g1/messages", json={"text": "hi"}, headers=self._headers())

        self.assertEqual(resp.status_code, 502)

    @patch("backend.routers.chats.load_chat_cookies")
    def test_take_over_marks_human_override_without_sending(self, mock_load_cookies):
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        self._auth_override()
        from backend.chatbot import config as chatbot_config
        chatbot_config.PROCESSED_CHATS.pop("g-takeover", None)

        resp = self.client.post("/api/chats/g-takeover/take-over", headers=self._headers())

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        self.assertEqual(chatbot_config.PROCESSED_CHATS.get("g-takeover"), "HUMAN_OVERRIDE")
        chatbot_config.PROCESSED_CHATS.pop("g-takeover", None)

    @patch("backend.routers.chats.discover_merchant_id")
    @patch("backend.chatbot.chat_client.http_session")
    @patch("backend.routers.chats.load_chat_cookies")
    def test_send_with_override_flag_marks_human_override(self, mock_load_cookies, mock_http, mock_discover):
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_discover.return_value = "20186986"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"StatusCode": 0, "data": {"id": "msg-1"}}
        mock_http.post.return_value = mock_resp
        self._auth_override()
        from backend.chatbot import config as chatbot_config
        chatbot_config.PROCESSED_CHATS.pop("g-quickreply", None)

        resp = self.client.post(
            "/api/chats/g-quickreply/messages",
            json={"text": "Добрый день!", "override": True},
            headers=self._headers(),
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(chatbot_config.PROCESSED_CHATS.get("g-quickreply"), "HUMAN_OVERRIDE")
        chatbot_config.PROCESSED_CHATS.pop("g-quickreply", None)


if __name__ == "__main__":
    unittest.main()
