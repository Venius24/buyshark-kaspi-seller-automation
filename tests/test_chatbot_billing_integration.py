import os
import sys
import shutil
import tempfile
import unittest
import asyncio
import datetime as _dt
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.append(os.getcwd())


def _fake_response(status_code=200, groups=None, sts=1000):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"data": {"groups": groups or [], "sts": sts}}
    return resp


def _one_group(msg_created_ms, msg_id="msg-1", group_id="group-1", sender="messenger"):
    return {
        "id": group_id,
        "subtitle": "Клиент",
        "title": "Товар",
        "lastMessage": {"id": msg_id, "created": msg_created_ms, "sender": sender, "text": "Здравствуйте"},
    }


class ChatbotBillingIntegrationTest(unittest.TestCase):
    """Интеграция credits.try_consume() с реальным путём воркера чат-бота
    (Слайс 3/6): при исчерпанных дневных кредитах generate_ai_reply НЕ
    вызывается, пишется лог пропуска, сообщение не переспрашивается вечно;
    заблокированный магазин пропускается ДО резолва куки; успешный ответ
    даёт credit_units_milli == дельте used_milli (сверочный инвариант)."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "chatbot_billing_test.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._session_patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._session_patch.start()

        from backend.db.models import Store, StoreTariffDB
        now = _dt.datetime.utcnow()
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        session.add(StoreTariffDB(
            store_id="store-a", plan_code="START", credit_addon_code="NONE",
            billing_status="ACTIVE", billing_anchor_day=15,
            period_start=now, period_end=now + _dt.timedelta(days=30),
            next_charge_at=now + _dt.timedelta(days=30), activated_at=now,
        ))
        session.commit()
        session.close()

        from backend.chatbot import worker as chatbot_worker
        self.chatbot_worker = chatbot_worker

        # bot_profile: band0 (множитель 1.0x), без human_triggers.
        self._profile_patch = patch.object(
            chatbot_worker, "get_bot_profile",
            return_value={"prompt_tier": "band0", "human_triggers": [], "custom_instructions": ""},
        )
        self._profile_patch.start()

        # Логистический шаблон никогда не срабатывает — идём по ИИ-ветке.
        self._logistics_patch = patch.object(chatbot_worker, "check_logistics_question", return_value=None)
        self._logistics_patch.start()

        self._match_trigger_patch = patch.object(chatbot_worker, "match_human_trigger", return_value=False)
        self._match_trigger_patch.start()

        self._chat_client_patch = patch.multiple(
            chatbot_worker.chat_client,
            send_typing=AsyncMock(return_value=None),
            send_message=AsyncMock(return_value={"success": True}),
            mark_read=AsyncMock(return_value=None),
        )
        self._chat_client_patch.start()

        self._history_patch = patch.object(chatbot_worker, "get_chat_history", AsyncMock(return_value=[]))
        self._history_patch.start()

        self._generate_patch = patch.object(
            chatbot_worker, "generate_ai_reply",
            AsyncMock(return_value=("Спасибо за обращение!", False, {"model": "deepseek", "prompt_tokens": 10, "completion_tokens": 5})),
        )
        self._generate_ai_reply = self._generate_patch.start()

    def tearDown(self):
        # is_service_blocked() caches per store_id for 30s in a module-global
        # dict (backend/billing/enforcement.py) — this test's "store-a" is
        # reused by unrelated test files in the same pytest process; a stale
        # "blocked" entry would otherwise leak into them.
        from backend.billing import enforcement
        enforcement._cache.clear()

        self._generate_patch.stop()
        self._history_patch.stop()
        self._chat_client_patch.stop()
        self._match_trigger_patch.stop()
        self._logistics_patch.stop()
        self._profile_patch.stop()
        self._session_patch.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _logs(self):
        from backend.db.models import ChatResponseLogDB
        session = self.state_helpers._get_sync_db_session()
        rows = session.query(ChatResponseLogDB).filter_by(store_id="store-a").all()
        session.expunge_all()
        session.close()
        return rows

    def test_exhausted_credits_skip_generate_ai_reply_and_logs(self):
        from backend.billing import credits as billing_credits
        # Исчерпываем дневной лимит START (50_000 milli) заранее.
        result = billing_credits.try_consume("store-a", 50_000)
        self.assertTrue(result.ok)

        now_ms = int(_dt.datetime.utcnow().timestamp() * 1000)
        resp = _fake_response(groups=[_one_group(now_ms)])
        with patch.object(self.chatbot_worker.http_session, "post", return_value=resp):
            ok = asyncio.run(self.chatbot_worker._run_chatbot_pass({}, "merchant-1", "store-a"))

        self.assertTrue(ok)
        self._generate_ai_reply.assert_not_called()
        logs = self._logs()
        kinds = [l.kind for l in logs]
        self.assertIn("inbound_skipped_no_credits", kinds)

    def test_exhausted_credits_does_not_loop_forever_on_same_message(self):
        """mark_chat_replied должен сработать ДО резерва кредита — иначе
        5-секундный цикл вечно переоценивал бы одно и то же сообщение."""
        from backend.billing import credits as billing_credits
        billing_credits.try_consume("store-a", 50_000)

        now_ms = int(_dt.datetime.utcnow().timestamp() * 1000)
        resp = _fake_response(groups=[_one_group(now_ms)])
        with patch.object(self.chatbot_worker.http_session, "post", return_value=resp):
            asyncio.run(self.chatbot_worker._run_chatbot_pass({}, "merchant-1", "store-a"))

        from backend.chatbot import config
        self.assertEqual(config.get_chat_last_replied("store-a", "group-1"), "msg-1")

    def test_successful_reply_credit_units_milli_matches_used_delta(self):
        from backend.billing import credits as billing_credits
        before = billing_credits.today_usage("store-a")

        now_ms = int(_dt.datetime.utcnow().timestamp() * 1000)
        resp = _fake_response(groups=[_one_group(now_ms)])
        with patch.object(self.chatbot_worker.http_session, "post", return_value=resp):
            ok = asyncio.run(self.chatbot_worker._run_chatbot_pass({}, "merchant-1", "store-a"))

        self.assertTrue(ok)
        self._generate_ai_reply.assert_called_once()
        after = billing_credits.today_usage("store-a")
        delta = after.used_milli - before.used_milli

        logs = self._logs()
        reply_logs = [l for l in logs if l.kind == "inbound_reply"]
        self.assertEqual(len(reply_logs), 1)
        self.assertEqual(reply_logs[0].credit_units_milli, delta)
        self.assertEqual(delta, 1000)  # band0 = 1.0x

    def test_blocked_store_skipped_before_cookie_resolution(self):
        from backend.billing import ledger, engine as billing_engine
        ledger.debit("store-a", 300_000, "adjustment", idempotency_key="block-it")
        status = billing_engine.evaluate_block("store-a")
        self.assertEqual(status, "BLOCKED_BALANCE")

        store = MagicMock()
        store.store_id = "store-a"
        store.token = "tok"

        list_stores_patch = patch.object(self.chatbot_worker, "list_active_stores", AsyncMock(return_value=[store]))
        load_cookies_patch = patch.object(self.chatbot_worker, "load_chat_cookies")
        cfg_patch = patch.object(
            self.chatbot_worker.config, "load_chatbot_config", return_value={"enabled": True}
        )
        with list_stores_patch, load_cookies_patch as mock_load_cookies, cfg_patch:
            asyncio.run(self.chatbot_worker.run_chatbot_cycle())
            mock_load_cookies.assert_not_called()


if __name__ == "__main__":
    unittest.main()
