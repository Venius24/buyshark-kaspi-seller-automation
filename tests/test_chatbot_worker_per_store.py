import os
import sys
import threading
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


class TestDiffCursor(unittest.IsolatedAsyncioTestCase):
    """getDiffGroups отдаёт диалоги от старых изменений к новым. Курсор обязан
    сдвигаться по обработанной странице — иначе после первых N изменившихся
    диалогов бот перестаёт видеть новые сообщения (прод 01–02.10.2026)."""

    def setUp(self):
        worker._chatbot_state.clear()

    async def _pass(self, groups, more=False):
        response = MagicMock(status_code=200)
        response.json.return_value = {"data": {"groups": groups, "more": more, "sts": 9_999}}
        with patch.object(worker.http_session, "post", return_value=response) as post,              patch.object(worker.config, "get_chat_states", return_value={}),              patch.object(worker, "reset_cookie_alert"):
            await worker._run_chatbot_pass({}, "merchant", "store-a")
        return post.call_args.kwargs["json"]

    async def test_cursor_moves_to_newest_change_on_page(self):
        state = worker._get_chatbot_state("store-a")
        state["start_gts"], state["day_start_ts"] = 100, 10**15
        groups = [
            {"id": "a", "gts": 200, "lastMessage": {"id": "1", "created": 1, "sender": "client"}},
            {"id": "b", "gts": 300, "lastMessage": {"id": "2", "created": 1, "sender": "client"}},
        ]
        sent = await self._pass(groups, more=True)
        self.assertEqual(sent["gts"], 100)
        self.assertEqual(sent["limit"], worker.DIFF_PAGE_LIMIT)
        self.assertEqual(state["start_gts"], 300)

    async def test_cursor_stays_before_debounced_group(self):
        import time
        state = worker._get_chatbot_state("store-a")
        state["start_gts"], state["day_start_ts"] = 100, 0
        fresh = int(time.time() * 1000)
        groups = [
            {"id": "a", "gts": 200, "lastMessage": {"id": "1", "created": 1, "sender": "client"}},
            {"id": "typing", "gts": 250, "lastMessage": {"id": "2", "created": fresh, "sender": "messenger"}},
            {"id": "c", "gts": 300, "lastMessage": {"id": "3", "created": 1, "sender": "client"}},
        ]
        await self._pass(groups)
        self.assertEqual(state["start_gts"], 249)

    async def test_empty_page_jumps_to_server_ts(self):
        state = worker._get_chatbot_state("store-a")
        state["start_gts"] = 100
        await self._pass([])
        self.assertEqual(state["start_gts"], 9_999)


class TestRunChatbotCycle(unittest.IsolatedAsyncioTestCase):
    """Phase 7: run_chatbot_cycle() — the testable core of
    kaspi_chatbot_loop(), extracted from the infinite while-loop. Also
    verifies the delay-timing contract (5s normal/disabled, 10s cookie
    failure) survived the extraction unchanged."""

    def setUp(self):
        worker._chatbot_state.clear()

    async def test_pass_reads_states_once_outside_event_loop(self):
        worker._get_chatbot_state("store-a")["day_start_ts"] = 0
        groups = [
            {"id": "chat-1", "lastMessage": {"id": "msg-1", "created": 1, "sender": "messenger"}},
            {"id": "chat-2", "lastMessage": {"id": "msg-2", "created": 1, "sender": "messenger"}},
            {"id": "chatGuide", "lastMessage": {"id": "guide", "created": 1}},
            {"id": "old-chat", "lastMessage": {"id": "old", "created": -1}},
        ]
        response = MagicMock(status_code=200)
        response.json.return_value = {"data": {"groups": groups}}
        loop_thread = threading.get_ident()
        read_threads = []

        def read_states(store_id, group_ids):
            read_threads.append(threading.get_ident())
            self.assertEqual(store_id, "store-a")
            self.assertEqual(group_ids, ["chat-1", "chat-2"])
            return {
                "chat-1": {"human_override": True},
                "chat-2": {"last_replied_msg_id": "msg-2"},
            }

        with patch.object(worker.http_session, "post", return_value=response), \
             patch.object(worker.config, "get_chat_states", side_effect=read_states) as mock_states, \
             patch.object(worker, "reset_cookie_alert"), \
             patch.object(worker.chat_client, "send_message", new_callable=AsyncMock) as mock_send:
            result = await worker._run_chatbot_pass({}, "merchant", "store-a")

        self.assertTrue(result)
        mock_states.assert_called_once()
        self.assertNotEqual(read_threads, [loop_thread])
        mock_send.assert_not_awaited()

    @patch("backend.chatbot.worker.list_active_stores", new_callable=AsyncMock)
    @patch("backend.chatbot.worker.config")
    async def test_disabled_returns_5s_delay_without_touching_cookies(self, mock_config, mock_list_stores):
        # Пустой список сторов -> легаси-ветка run_chatbot_cycle(), которую
        # этот тест и проверяет (общий enabled-флаг без per-store БД).
        mock_list_stores.return_value = []
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
