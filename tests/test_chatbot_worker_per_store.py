import os
import sys
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.append(os.getcwd())

from backend.security.store_context import StoreContext
from backend.chatbot import worker


class TestChatbotState(unittest.TestCase):
    """Phase 7: per-store polling cursor (start_gts) — store A and store B
    page through their own chat feed independently, and don't reset each
    other's cursor on every tick."""

    def setUp(self):
        worker._chatbot_state.clear()

    def test_same_store_id_returns_the_same_persistent_state(self):
        state1 = worker._get_chatbot_state("store-a")
        state1["start_gts"] = 12345
        state2 = worker._get_chatbot_state("store-a")
        self.assertEqual(state2["start_gts"], 12345)

    def test_different_store_ids_get_independent_state(self):
        state_a = worker._get_chatbot_state("store-a")
        state_a["start_gts"] = 111
        state_b = worker._get_chatbot_state("store-b")
        state_b["start_gts"] = 222
        self.assertEqual(worker._get_chatbot_state("store-a")["start_gts"], 111)
        self.assertEqual(worker._get_chatbot_state("store-b")["start_gts"], 222)

    def test_none_store_id_is_the_legacy_key(self):
        state1 = worker._get_chatbot_state(None)
        state2 = worker._get_chatbot_state()
        self.assertIs(state1, state2)


class TestRunChatbotCycle(unittest.IsolatedAsyncioTestCase):
    """Phase 7: run_chatbot_cycle() — the testable core of
    kaspi_chatbot_loop(), extracted from the infinite while-loop. Also
    verifies the delay-timing contract (5s normal/disabled, 10s cookie
    failure) survived the extraction unchanged."""

    def setUp(self):
        worker._chatbot_state.clear()

    @patch("backend.chatbot.worker.config")
    async def test_disabled_returns_5s_delay_without_touching_cookies(self, mock_config):
        mock_config.load_chatbot_config.return_value = {"enabled": False}
        with patch("backend.chatbot.worker.load_chat_cookies") as mock_cookies:
            delay = await worker.run_chatbot_cycle()
        mock_cookies.assert_not_called()
        self.assertEqual(delay, 5)

    @patch("backend.chatbot.worker.notify_cookie_expired", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.load_chat_cookies")
    @patch("backend.chatbot.worker.config")
    async def test_missing_cookies_returns_10s_delay(self, mock_config, mock_load_cookies, mock_notify):
        mock_config.load_chatbot_config.return_value = {"enabled": True}
        mock_load_cookies.return_value = {}
        delay = await worker.run_chatbot_cycle()
        mock_notify.assert_awaited_once()
        self.assertEqual(delay, 10)

    @patch("backend.chatbot.worker._run_chatbot_pass", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.list_active_stores", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.load_chat_cookies")
    @patch("backend.chatbot.worker.config")
    async def test_no_stores_in_db_falls_back_to_legacy_session(
        self, mock_config, mock_load_cookies, mock_list_stores, mock_pass
    ):
        mock_config.load_chatbot_config.return_value = {"enabled": True, "merchant_id": "20186986"}
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_list_stores.return_value = []
        mock_pass.return_value = True

        delay = await worker.run_chatbot_cycle()

        mock_pass.assert_awaited_once_with({"mc-session": "s", "mc-sid": "sid"}, "20186986", None)
        self.assertEqual(delay, 5)

    @patch("backend.chatbot.worker.discover_merchant_id")
    @patch("backend.chatbot.worker._run_chatbot_pass", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.list_active_stores", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.load_chat_cookies")
    @patch("backend.chatbot.worker.config")
    async def test_iterates_every_active_store_with_dynamically_resolved_merchant_id(
        self, mock_config, mock_load_cookies, mock_list_stores, mock_pass, mock_discover
    ):
        """The critical bug this refactor must avoid: Store.merchant_id in the
        DB can be a synthetic hash fallback (e.g. 'm_87776f40'), not the real
        numeric Kaspi merchant ID the chat API's x-merchant-id header needs.
        merchant_id must come from discover_merchant_id(store.token), never
        from store.merchant_id directly."""
        mock_config.load_chatbot_config.return_value = {"enabled": True, "merchant_id": "20186986"}
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_fallback_hash_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_fallback_hash_b", store_id="store-b"),
        ]
        mock_discover.side_effect = ["20186986", "40297531"]
        mock_pass.return_value = True

        await worker.run_chatbot_cycle()

        mock_discover.assert_any_call("tok_a")
        mock_discover.assert_any_call("tok_b")
        self.assertEqual(mock_pass.await_args_list[0].args[1], "20186986")
        self.assertEqual(mock_pass.await_args_list[0].args[2], "store-a")
        self.assertEqual(mock_pass.await_args_list[1].args[1], "40297531")
        self.assertEqual(mock_pass.await_args_list[1].args[2], "store-b")

    @patch("backend.chatbot.worker.discover_merchant_id")
    @patch("backend.chatbot.worker._run_chatbot_pass", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.list_active_stores", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.load_chat_cookies")
    @patch("backend.chatbot.worker.config")
    async def test_unresolvable_merchant_id_skips_store_not_another_stores_id(
        self, mock_config, mock_load_cookies, mock_list_stores, mock_pass, mock_discover
    ):
        # Раньше при неудачном discover_merchant_id код тихо подставлял
        # cfg["merchant_id"] (который сам мог по умолчанию быть "20186986" —
        # реальным ID Barkhan) — чужой стор начинал работать под его личностью.
        # Теперь при неопределённом merchant_id стор просто пропускается в
        # этом тике (следующий тик может определить его успешно).
        mock_config.load_chatbot_config.return_value = {"enabled": True, "merchant_id": "20186986"}
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_hash", store_id="store-a"),
        ]
        mock_discover.return_value = None  # resolution failed
        mock_pass.return_value = True

        await worker.run_chatbot_cycle()

        mock_pass.assert_not_awaited()

    @patch("backend.chatbot.worker.discover_merchant_id")
    @patch("backend.chatbot.worker._run_chatbot_pass", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.list_active_stores", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.load_chat_cookies")
    @patch("backend.chatbot.worker.config")
    async def test_one_store_failing_does_not_block_the_others(
        self, mock_config, mock_load_cookies, mock_list_stores, mock_pass, mock_discover
    ):
        """Store A's discover_merchant_id blowing up (e.g. Kaspi REST API
        down for that token) must not prevent store B's pass from running."""
        mock_config.load_chatbot_config.return_value = {"enabled": True, "merchant_id": "20186986"}
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        mock_discover.side_effect = [Exception("REST API down"), "40297531"]
        mock_pass.return_value = True

        await worker.run_chatbot_cycle()

        mock_pass.assert_awaited_once()
        self.assertEqual(mock_pass.await_args_list[0].args[1], "40297531")
        self.assertEqual(mock_pass.await_args_list[0].args[2], "store-b")

    @patch("backend.chatbot.worker.discover_merchant_id")
    @patch("backend.chatbot.worker._run_chatbot_pass", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.list_active_stores", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.load_chat_cookies")
    @patch("backend.chatbot.worker.config")
    async def test_http_failure_in_one_store_pass_returns_10s_without_raising(
        self, mock_config, mock_load_cookies, mock_list_stores, mock_pass, mock_discover
    ):
        mock_config.load_chatbot_config.return_value = {"enabled": True, "merchant_id": "20186986"}
        mock_load_cookies.return_value = {"mc-session": "s", "mc-sid": "sid"}
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
        ]
        mock_discover.return_value = "20186986"
        mock_pass.return_value = False  # _run_chatbot_pass signals an HTTP failure

        delay = await worker.run_chatbot_cycle()

        self.assertEqual(delay, 10)


if __name__ == "__main__":
    unittest.main()
