"""Качество автоответчика (2026-10-02): триггеры, данные заказа, история,
журнал. См. HANDOFF §3.107."""
import os
import sys
import shutil
import tempfile
import time
import unittest
import asyncio
import datetime as _dt
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.append(os.getcwd())

import backend.chatbot.worker  # noqa: F401 — разрывает циклический импорт kaspi <-> chat_client
from backend.chatbot.prompt import (
    find_human_triggers, match_human_trigger, looks_like_complaint, find_escalation_reason, promises_manager,
    DEFAULT_HUMAN_TRIGGERS,
)
from backend.chatbot.kaspi import check_logistics_question
from backend.chatbot.ai import build_dialog
from backend.chatbot import order_context
from backend.chatbot.order_context import ChatFacts, parse_order, render_facts_block


class TriggerMatchingTest(unittest.TestCase):
    def test_stems_match_word_endings(self):
        # Жалоба с прода: стемы из списка по умолчанию не ловили окончания.
        for text in ("Товар пришёл сломанный", "Коробка разбитая", "Бракованный товар"):
            self.assertTrue(match_human_trigger(text, DEFAULT_HUMAN_TRIGGERS), text)

    def test_short_triggers_stay_whole_word(self):
        self.assertFalse(match_human_trigger("где штрих-код?", ["шт"]))
        self.assertTrue(match_human_trigger("нужно 3 шт", ["шт"]))
        self.assertFalse(match_human_trigger("судак свежий", ["суд"]))

    def test_trigger_not_matched_inside_word(self):
        self.assertFalse(match_human_trigger("небрак", ["брак"]))

    def test_find_returns_all_matched(self):
        self.assertEqual(find_human_triggers("есть ли в наличии?", DEFAULT_HUMAN_TRIGGERS), ["есть ли", "в наличии"])

    def test_complaint_with_courier_does_not_get_logistics_template(self):
        for text in ("Товар пришёл сломанный, доставили вчера", "Коробка разбитая, курьер бросил", "Сынған келді, курьер"):
            self.assertTrue(looks_like_complaint(text), text)
            self.assertIsNone(check_logistics_question(text), text)

    def test_plain_logistics_question_still_gets_template(self):
        self.assertIsNotNone(check_logistics_question("Когда приедет курьер?"))
        self.assertIsNotNone(check_logistics_question("Где заказ? Курьеру звонила"))


class EscalationStemsTest(unittest.TestCase):
    """Вызов менеджера мимо модели (HANDOFF §3.110)."""

    def test_requests_bot_cannot_solve_escalate(self):
        for text in ["Хочу отменить заказ", "Почему отменили мой заказ??", "Курьер привёз вскрытую упаковку",
                     "Хочу взять 20 штук оптом, сделаете скидку?", "Вы продали подделку", "Позовите оператора",
                     "Заказ пришёл пустой", "С меня списали деньги дважды", "Басқа түсті жіберіпсіздер"]:
            self.assertIsNotNone(find_escalation_reason(text), text)

    def test_yo_is_normalized(self):
        self.assertIsNotNone(find_escalation_reason("Пришёл с трещиной"))

    def test_ordinary_questions_do_not_escalate(self):
        for text in ["Здравствуйте, когда доставят?", "Спасибо, всё пришло, всё отлично", "Подходит ли для iPhone 15?",
                     "Работаете в субботу?", "Рахмет", "Это оригинал?", "Какой цвет есть?"]:
            self.assertIsNone(find_escalation_reason(text), text)

    def test_manager_promise_detected(self):
        self.assertTrue(promises_manager("Сейчас передам вопрос менеджеру, он свяжется с вами."))
        self.assertTrue(promises_manager("Уточню и вернусь с ответом."))
        self.assertIsNone(promises_manager("Да, есть в наличии."))
        self.assertIsNone(promises_manager("Уточните, пожалуйста, цвет."))


def _order_json(**attr_overrides):
    attr = {
        "code": "1025596212", "state": "KASPI_DELIVERY", "status": "ACCEPTED_BY_MERCHANT",
        "deliveryMode": "DELIVERY_LOCAL", "isKaspiDelivery": True, "assembled": True,
        "creationDate": 1786092743539, "totalPrice": 4600.0, "pickupPointId": "20186986_001",
        "customer": {"name": "Иван", "cellPhone": "7771234567"},
        "deliveryAddress": {"formattedAddress": "Алматы, ул. Секретная 1"},
        "kaspiDelivery": {"courierTransmissionDate": 1786094901000, "express": False},
    }
    attr.update(attr_overrides)
    return {
        "data": [{"id": "int-1", "attributes": attr, "relationships": {"entries": {"data": [{"id": "e1"}]}}}],
        "included": [{"id": "e1", "type": "orderentries", "attributes": {
            "quantity": 2, "offer": {"code": "D970-0025-1", "name": "Пеногенератор M-540502"}}}],
    }


class OrderFactsTest(unittest.TestCase):
    def test_parse_order_has_no_personal_data(self):
        order = parse_order(_order_json())
        dumped = repr(order)
        self.assertNotIn("Иван", dumped)
        self.assertNotIn("7771234567", dumped)
        self.assertNotIn("Секретная", dumped)
        self.assertEqual(order["entries"], [{"name": "Пеногенератор M-540502", "sku": "D970-0025-1", "quantity": 2}])

    def test_pickup_vs_postomat_by_is_kaspi_delivery(self):
        # deliveryMode у обоих DELIVERY_PICKUP — различитель isKaspiDelivery (CLAUDE.md).
        pickup = parse_order(_order_json(deliveryMode="DELIVERY_PICKUP", isKaspiDelivery=False, kaspiDelivery=None))
        postomat = parse_order(_order_json(deliveryMode="DELIVERY_PICKUP", isKaspiDelivery=True))
        self.assertEqual(pickup["delivery"], "Самовывоз")
        self.assertFalse(pickup["kaspi_carries"])
        self.assertEqual(postomat["delivery"], "Postomat")
        self.assertTrue(postomat["kaspi_carries"])

    def test_handed_to_courier_only_when_transmitted(self):
        transmitted = ChatFacts(order_code="1", order=parse_order(_order_json()))
        assembling = ChatFacts(order_code="1", order=parse_order(_order_json(assembled=False, kaspiDelivery={})))
        self.assertTrue(transmitted.handed_to_courier)
        self.assertFalse(assembling.handed_to_courier)
        self.assertIn("ещё собирается", render_facts_block(assembling))
        self.assertIn("передал заказ курьеру Kaspi", render_facts_block(transmitted))

    def test_block_without_order_and_with_failed_lookup(self):
        self.assertIn("не привязан к заказу", render_facts_block(ChatFacts()))
        failed = render_facts_block(ChatFacts(order_code="777", order_lookup_failed=True))
        self.assertIn("недоступны", failed)

    def test_stock_known_and_unknown(self):
        facts = ChatFacts(products=[{"name": "A", "sku": "1", "stock": 3}])
        self.assertTrue(facts.stock_known)
        self.assertIn("в наличии", render_facts_block(facts))
        unknown = ChatFacts(products=[{"name": "A", "sku": "1", "stock": None}])
        self.assertFalse(unknown.stock_known)
        self.assertIn("наличие неизвестно", render_facts_block(unknown))
        self.assertFalse(ChatFacts().stock_known)

    def test_lookup_products_by_order_sku(self):
        catalog = [
            {"name": "Пеногенератор", "merchantSku": "D970-0025-1", "stock": "4"},
            {"name": "Другое", "merchantSku": "X", "stock": None},
        ]
        with patch("backend.state_helpers._load_products", return_value=catalog), \
             patch("backend.state_helpers._apply_overrides", side_effect=lambda p, s: p):
            found = order_context.lookup_products("store-a", ["D970-0025-1", "missing"])
        self.assertEqual(found, [{"name": "Пеногенератор", "sku": "D970-0025-1", "stock": 4}])


class DialogTest(unittest.TestCase):
    def test_roles_media_and_merge(self):
        raw = [
            {"sender": "messenger", "text": "Здравствуйте", "created": 1},
            {"sender": "messenger", "messageType": "IMAGE", "created": 2},
            {"sender": "client", "text": "Добрый день!", "created": 3},
        ]
        dialog = build_dialog(raw)
        self.assertEqual(dialog, [
            {"role": "user", "content": "Здравствуйте\n[клиент прислал фото]"},
            {"role": "assistant", "content": "Добрый день!"},
        ])

    def test_manager_reply_marked_only_after_tracking_start(self):
        since = _dt.datetime(2026, 10, 2, 0, 0)
        since_ms = int(since.replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
        raw = [
            {"sender": "client", "text": "старый ответ", "created": since_ms - 1000},
            {"sender": "messenger", "text": "?", "created": since_ms + 1},
            {"sender": "client", "text": "ответ бота", "created": since_ms + 2},
            {"sender": "messenger", "text": "??", "created": since_ms + 3},
            {"sender": "client", "text": "Оформим возврат", "created": since_ms + 4},
            {"sender": "messenger", "text": "ок", "created": since_ms + 5},
        ]
        dialog = build_dialog(raw, {"ответ бота"}, since)
        contents = [d["content"] for d in dialog if d["role"] == "assistant"]
        self.assertEqual(contents, ["старый ответ", "ответ бота", "[ответил менеджер магазина] Оформим возврат"])


def _fake_diff(groups):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": {"groups": groups, "sts": 1000}}
    return resp


class WorkerJournalTest(unittest.TestCase):
    """Сквозной проход воркера на SQLite: что уходит клиенту и что пишется в журнал."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "chatbot_quality.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self.state_helpers = state_helpers
        self._patches = [patch.object(state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine))]
        self._patches[0].start()

        from backend.db.models import Store, StoreTariffDB
        now = _dt.datetime.utcnow()
        session = state_helpers._get_sync_db_session()
        session.add(Store(id="store-q", merchant_id="m_q", name="Магазин Q"))
        session.add(StoreTariffDB(
            store_id="store-q", plan_code="START", billing_status="ACTIVE", billing_anchor_day=15,
            period_start=now, period_end=now + _dt.timedelta(days=30),
            next_charge_at=now + _dt.timedelta(days=30), activated_at=now,
        ))
        session.commit()
        session.close()

        from backend.chatbot import worker
        self.worker = worker
        self.sent = AsyncMock(return_value={"success": True})
        self.escalate = AsyncMock(return_value="HOLD")
        self.generate = AsyncMock(return_value=("Да, есть в наличии.", False, {"model": "deepseek-chat", "prompt_tokens": 100, "completion_tokens": 10}))
        self.facts = ChatFacts()
        self.history = []
        more = [
            patch.object(worker, "get_bot_profile", return_value={"prompt_tier": "band0", "human_triggers": DEFAULT_HUMAN_TRIGGERS}),
            patch.multiple(worker.chat_client, send_typing=AsyncMock(), send_message=self.sent, mark_read=AsyncMock(),
                           get_history=AsyncMock(side_effect=lambda *a, **k: self.history)),
            patch.object(worker, "notify_manager_and_block", self.escalate),
            patch.object(worker, "generate_ai_reply", self.generate),
            patch.object(worker, "collect_chat_facts", AsyncMock(side_effect=lambda *a: self.facts)),
            patch.object(worker, "REPLY_DEBOUNCE_SECONDS", 0),
        ]
        for p in more:
            p.start()
        self._patches += more

    def tearDown(self):
        from backend.billing import enforcement
        enforcement._cache.clear()
        for p in reversed(self._patches):
            p.stop()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _run(self, text, created_ms=None):
        created_ms = created_ms or int(time.time() * 1000) - 60_000
        self.history = [{"sender": "messenger", "text": text, "created": created_ms}]
        group = {"id": "g-1", "subtitle": "Клиент", "title": "Пеногенератор",
                 "analytics": {"order_id": "1025596212"},
                 "lastMessage": {"id": "m-1", "created": created_ms, "sender": "messenger", "text": text}}
        with patch.object(self.worker.http_session, "post", return_value=_fake_diff([group])):
            return asyncio.run(self.worker._run_chatbot_pass({}, "merchant-1", "store-q", token="tok"))

    def _logs(self):
        from backend.db.models import ChatResponseLogDB
        session = self.state_helpers._get_sync_db_session()
        rows = session.query(ChatResponseLogDB).filter_by(store_id="store-q").all()
        session.expunge_all()
        session.close()
        return rows

    def test_complaint_escalates_and_is_journaled(self):
        self._run("Товар пришёл сломанный, курьер бросил")
        self.escalate.assert_awaited_once()
        self.generate.assert_not_awaited()
        [row] = self._logs()
        self.assertEqual(row.kind, "inbound_escalated_trigger")
        self.assertEqual(row.client_text, "Товар пришёл сломанный, курьер бросил")
        self.assertEqual(row.reply_text, "HOLD")
        self.assertIn("сломан", row.detail)
        self.assertEqual(row.billed_units, 0)

    def test_stock_question_answered_by_bot_when_stock_known(self):
        self.facts = ChatFacts(order_code="1025596212", products=[{"name": "Пеногенератор", "sku": "X", "stock": 5}])
        self._run("Есть ли в наличии ещё 2 штуки?")
        self.escalate.assert_not_awaited()
        self.generate.assert_awaited_once()
        self.assertIn("в наличии", self.generate.await_args.kwargs["facts_block"])
        [row] = self._logs()
        self.assertEqual(row.kind, "inbound_reply")
        self.assertEqual(row.reply_text, "Да, есть в наличии.")
        self.assertEqual(row.order_code, "1025596212")

    def test_stock_question_escalates_when_stock_unknown(self):
        self._run("Есть ли в наличии?")
        self.escalate.assert_awaited_once()

    def test_logistics_template_skipped_while_order_assembling(self):
        self.facts = ChatFacts(order_code="1025596212", order=parse_order(_order_json(assembled=False, kaspiDelivery={})))
        self._run("Когда приедет курьер?")
        self.generate.assert_awaited_once()
        [row] = self._logs()
        self.assertEqual(row.kind, "inbound_reply")

    def test_logistics_template_sent_when_handed_to_courier(self):
        self.facts = ChatFacts(order_code="1025596212", order=parse_order(_order_json()))
        self._run("Когда приедет курьер?")
        self.generate.assert_not_awaited()
        [row] = self._logs()
        self.assertEqual(row.kind, "inbound_logistics_template")
        self.assertTrue(row.reply_text)

    def test_fresh_message_waits_for_client_to_finish(self):
        with patch.object(self.worker, "REPLY_DEBOUNCE_SECONDS", 15):
            self._run("Здравствуйте", created_ms=int(time.time() * 1000))
        self.generate.assert_not_awaited()
        self.escalate.assert_not_awaited()
        self.assertEqual(self._logs(), [])

    def test_return_request_escalates_without_model(self):
        self._run("Как оформить возврат?")
        self.escalate.assert_awaited_once()
        self.generate.assert_not_awaited()

    def test_cancel_request_escalates_without_model(self):
        self._run("Хочу отменить заказ")
        self.generate.assert_not_awaited()
        [row] = self._logs()
        self.assertEqual(row.kind, "inbound_escalated_trigger")
        self.assertIn("отмен", row.detail)

    def test_client_photo_escalates(self):
        created = int(time.time() * 1000) - 60_000
        self.history = [
            {"sender": "merchant", "text": "Здравствуйте!", "created": created - 5000},
            {"sender": "messenger", "messageType": "IMAGE", "text": "", "created": created - 1000},
            {"sender": "messenger", "text": "Вот так пришло", "created": created},
        ]
        group = {"id": "g-2", "subtitle": "Клиент", "title": "Пеногенератор", "analytics": {},
                 "lastMessage": {"id": "m-2", "created": created, "sender": "messenger", "text": "Вот так пришло"}}
        with patch.object(self.worker.http_session, "post", return_value=_fake_diff([group])):
            asyncio.run(self.worker._run_chatbot_pass({}, "merchant-1", "store-q", token="tok"))
        self.escalate.assert_awaited_once()
        self.generate.assert_not_awaited()

    def _run_history(self, history):
        created = history[-1]["created"]
        self.history = history
        group = {"id": "g-3", "subtitle": "Клиент", "title": "Пеногенератор", "analytics": {},
                 "lastMessage": {"id": "m-3", "created": created, "sender": "messenger", "text": history[-1].get("text", "")}}
        with patch.object(self.worker.http_session, "post", return_value=_fake_diff([group])):
            asyncio.run(self.worker._run_chatbot_pass({}, "merchant-1", "store-q", token="tok"))

    def test_voice_message_goes_to_model_rule_8(self):
        created = int(time.time() * 1000) - 60_000
        self._run_history([{"sender": "messenger", "messageType": "AUDIO", "text": "", "created": created}])
        self.escalate.assert_not_awaited()
        self.generate.assert_awaited_once()

    def test_unrecognized_attachment_escalates(self):
        created = int(time.time() * 1000) - 60_000
        self._run_history([{"sender": "messenger", "messageType": "FILE", "text": "", "created": created}])
        self.escalate.assert_awaited_once()
        self.generate.assert_not_awaited()
        [row] = self._logs()
        self.assertIn("вложение", row.detail)

    def test_empty_message_escalates(self):
        created = int(time.time() * 1000) - 60_000
        self._run_history([{"sender": "messenger", "messageType": "TEXT", "text": "", "created": created}])
        self.escalate.assert_awaited_once()
        self.generate.assert_not_awaited()

    def test_model_saying_message_is_empty_escalates(self):
        self.generate.return_value = ("Здравствуйте! Ваше сообщение пустое, напишите, пожалуйста, вопрос.", False, {"model": "deepseek-chat"})
        self._run("Подходит ли для iPhone 15?")
        self.escalate.assert_awaited_once()
        self.sent.assert_not_awaited()

    def test_model_promising_manager_without_marker_escalates(self):
        self.generate.return_value = ("Передам вопрос менеджеру, он свяжется с вами.", False, {"model": "deepseek-chat"})
        self._run("Подходит ли для iPhone 15?")
        self.escalate.assert_awaited_once()
        self.sent.assert_not_awaited()
        [row] = self._logs()
        self.assertEqual(row.kind, "inbound_escalated_ai")

    def test_plain_question_answered_by_model(self):
        self._run("Подходит ли для iPhone 15?")
        self.escalate.assert_not_awaited()
        self.sent.assert_awaited()

    def test_journal_summary(self):
        self._run("Товар пришёл сломанный")
        journal = self.state_helpers._load_bot_journal("store-q", days=7)
        self.assertEqual(journal["handled"], 1)
        self.assertEqual(journal["escalated"], 1)
        self.assertEqual(journal["escalation_rate"], 1.0)
        self.assertEqual(journal["items"][0]["client_text"], "Товар пришёл сломанный")
        only = self.state_helpers._load_bot_journal("store-q", days=7, kind="inbound_reply")
        self.assertEqual(only["items"], [])


if __name__ == "__main__":
    unittest.main()
