"""
Тесты единого писателя цен/остатков и учётчика квоты Kaspi.

Все числа в тестах опираются на живой замер прода 2026-08-12:
лимит OFFER=250 за фиксированное 30-минутное окно, batch-эндпоинт считается
ПОШТУЧНО (пачка из 8 сдвинула uploadedCount на 8), квоту тратит только
/pricefeed/upload/merchant/process.
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

from backend.services import offer_sync
from backend.services.kaspi_quota import (
    KaspiOfferQuota, QuotaState, P0_RESERVE, _parse_quota, _parse_window_seconds,
)
from backend.services.offer_sync import Priority, should_push_stock, stock_step


class TestStockLadder(unittest.TestCase):
    """Лестница шагов остатка (пункт 9 пользователя)."""

    def test_steps_match_agreed_table(self):
        self.assertEqual(stock_step(1006), 100)
        self.assertEqual(stock_step(500), 100)
        self.assertEqual(stock_step(499), 50)
        self.assertEqual(stock_step(150), 50)
        self.assertEqual(stock_step(149), 10)
        self.assertEqual(stock_step(30), 10)
        self.assertEqual(stock_step(29), 5)
        self.assertEqual(stock_step(20), 5)
        self.assertEqual(stock_step(19), 1)
        self.assertEqual(stock_step(0), 1)

    def test_big_stock_ignores_small_drop(self):
        # Ровно тот случай, что жёг квоту на проде: 1006 -> 998 по группе из 14 SKU.
        self.assertFalse(should_push_stock(1006, 998))

    def test_big_stock_pushes_after_full_step(self):
        self.assertTrue(should_push_stock(1006, 905))

    def test_step_uses_lower_of_the_two_values(self):
        # Дыра, которую закрывает min(): отправили 160, реально 115. Разница 45,
        # шаг "по 160" = 50 -> промолчали бы, оставив витрину врать на 45 штук.
        # Шаг "по 115" = 10 -> запись уходит.
        self.assertTrue(should_push_stock(160, 115))

    def test_small_stock_is_exact(self):
        self.assertTrue(should_push_stock(12, 11))
        self.assertTrue(should_push_stock(3, 2))

    def test_crossing_stop_qty_always_pushes(self):
        # Переход через стоп кол-во лестницу игнорирует — это P0.
        self.assertTrue(should_push_stock(600, 597, stop_qty=598))
        self.assertTrue(should_push_stock(7, 5, stop_qty=5))

    def test_crossing_zero_always_pushes(self):
        self.assertTrue(should_push_stock(600, 0))
        self.assertTrue(should_push_stock(0, 600))

    def test_no_change_never_pushes(self):
        self.assertFalse(should_push_stock(1006, 1006))
        self.assertFalse(should_push_stock(7, 7))

    def test_unknown_last_value_pushes(self):
        self.assertTrue(should_push_stock(None, 42))


class TestQuota(unittest.TestCase):
    def test_parses_real_kaspi_payload(self):
        state = _parse_quota({
            "merchantUid": "20186986", "unlimited": False, "limitTime": "30m",
            "limits": {"FILE": 2, "OFFER": 250},
            "statistics": [{"documentType": "OFFER", "uploadedCount": 49,
                            "expirationDate": "2026-08-12T17:31:26.494"}],
        })
        self.assertEqual(state.limit, 250)
        self.assertEqual(state.used, 49)
        self.assertEqual(state.remaining, 201)
        self.assertEqual(state.reset_at, datetime.datetime(2026, 8, 12, 17, 31, 26, 494000))

    def test_reserve_protects_p0(self):
        state = QuotaState(limit=250, used=230, known=True)
        self.assertEqual(state.remaining, 20)
        # Обычные записи уже за отсечкой...
        self.assertEqual(state.budget_for(Priority.LOSING), 0)
        # ...а снятие с продажи всё ещё может пройти.
        self.assertEqual(state.budget_for(Priority.WITHDRAW), 20)

    def test_reserve_only_bites_near_the_end(self):
        state = QuotaState(limit=250, used=0, known=True)
        self.assertEqual(state.budget_for(Priority.MINOR), 250 - P0_RESERVE)
        self.assertEqual(state.budget_for(Priority.WITHDRAW), 250)

    def test_unreadable_quota_is_treated_as_exhausted(self):
        # Ошибиться в сторону "не пишем" дешевле, чем словить 429.
        q = KaspiOfferQuota()
        with patch.object(q, "_fetch", side_effect=RuntimeError("boom")):
            state = q.get("store-1", "999")
        self.assertFalse(state.known)
        self.assertEqual(state.remaining, 0)

    def test_cached_window_is_reread_after_it_expires(self):
        q = KaspiOfferQuota()
        past = datetime.datetime.now() - datetime.timedelta(seconds=1)
        q._states["store-1"] = QuotaState(
            limit=250, used=250, reset_at=past,
            fetched_at=datetime.datetime.now(), known=True,
        )
        fresh = QuotaState(limit=250, used=0, reset_at=None,
                           fetched_at=datetime.datetime.now(), known=True)
        with patch.object(q, "_fetch", return_value=fresh) as mock_fetch:
            state = q.get("store-1", "999")
        mock_fetch.assert_called_once()
        self.assertEqual(state.used, 0)

    def test_seconds_until_reset(self):
        soon = datetime.datetime.now() + datetime.timedelta(minutes=10)
        self.assertAlmostEqual(QuotaState(reset_at=soon).seconds_until_reset(), 600, delta=2)


class OfferSyncDBTestCase(unittest.TestCase):
    """Общая изоляция на временной SQLite."""

    def setUp(self):
        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        self._tmp = tempfile.mkdtemp()
        path = os.path.join(self._tmp, "offer_sync.db").replace(os.sep, "/")
        self._engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        self._patch = patch.object(
            state_helpers, "_get_sync_db_session", side_effect=lambda: Session(self._engine)
        )
        self._patch.start()
        offer_sync.reset_flap_state()

    def tearDown(self):
        self._patch.stop()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _row(self, sku="SKU-1", store="s1"):
        from backend.db.models import KaspiOfferSyncDB
        from sqlalchemy.orm import Session
        with Session(self._engine) as s:
            return s.get(KaspiOfferSyncDB, (store, sku))

    def _seed_pushed(self, sku="SKU-1", store="s1", price=1000, stock=10):
        from backend.db.models import KaspiOfferSyncDB
        from sqlalchemy.orm import Session
        now = datetime.datetime.utcnow()
        with Session(self._engine) as s:
            s.add(KaspiOfferSyncDB(
                store_id=store, sku=sku, master_sku="M1", model="Товар",
                store_suffix="001", city_id="750000000",
                pushed_price=price, pushed_stock=stock, pushed_at=now,
                kaspi_price=price, kaspi_stock=stock, kaspi_seen_at=now,
                priority=Priority.MINOR,
            ))
            s.commit()


class TestEnqueueCoalescing(OfferSyncDBTestCase):
    def test_repeated_intents_collapse_to_the_last_value(self):
        # Пять заказов подряд по одной группе -> одно намерение с финальным
        # остатком, а не пять записей квоты.
        for stock in (100, 98, 95, 90, 88):
            offer_sync.enqueue("s1", "SKU-1", stock=stock, reason="group:g1")
        row = self._row()
        self.assertEqual(row.desired_stock, 88)

    def test_priority_escalates_but_never_degrades(self):
        offer_sync.enqueue("s1", "SKU-1", stock=10, priority=Priority.MINOR)
        offer_sync.enqueue("s1", "SKU-1", stock=0, priority=Priority.WITHDRAW)
        self.assertEqual(self._row().priority, Priority.WITHDRAW)
        offer_sync.enqueue("s1", "SKU-1", stock=5, priority=Priority.MINOR)
        self.assertEqual(self._row().priority, Priority.WITHDRAW)

    def test_intent_age_counts_from_first_declaration(self):
        offer_sync.enqueue("s1", "SKU-1", stock=10)
        first = self._row().desired_at
        offer_sync.enqueue("s1", "SKU-1", stock=9)
        self.assertEqual(self._row().desired_at, first)

    def test_empty_intent_is_ignored(self):
        offer_sync.enqueue("s1", "SKU-1")
        self.assertIsNone(self._row())

    def test_last_known_stock_prefers_pending_intent(self):
        self._seed_pushed(stock=50)
        self.assertEqual(offer_sync.last_known_stock("s1", "SKU-1"), 50)
        offer_sync.enqueue("s1", "SKU-1", stock=30)
        self.assertEqual(offer_sync.last_known_stock("s1", "SKU-1"), 30)


class TestFlush(OfferSyncDBTestCase):
    def _mock_ok_batch(self, ids=1):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [{"id": f"tx{i}"} for i in range(ids)]
        return resp

    def test_no_op_intent_is_dropped_without_spending_quota(self):
        # Главный источник потерь: Kaspi помечал такие наши записи NO_CHANGE.
        self._seed_pushed(price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-1", price=1000, stock=10)
        with patch("backend.state_helpers._mc_api_request") as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        req.assert_not_called()
        self.assertEqual(stats["no_op"], 1)
        self.assertEqual(stats["written"], 0)
        self.assertIsNone(self._row().desired_at)

    def test_price_change_below_threshold_is_dropped(self):
        # Порог 2 ₸: разница в рубль не стоит единицы квоты и открывает
        # пинг-понг с чужим ботом.
        self._seed_pushed(price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-1", price=999, stock=10)
        with patch("backend.state_helpers._mc_api_request") as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        req.assert_not_called()
        self.assertEqual(stats["no_op"], 1)

    def test_real_change_is_written_in_one_batch(self):
        self._seed_pushed(price=1000, stock=10)
        self._seed_pushed(sku="SKU-2", price=500, stock=4)
        offer_sync.enqueue("s1", "SKU-1", price=900, stock=10)
        offer_sync.enqueue("s1", "SKU-2", price=480, stock=4)
        with patch("backend.state_helpers._mc_api_request", return_value=self._mock_ok_batch(2)) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        self.assertEqual(stats["written"], 2)
        # Ровно ОДИН HTTP-запрос на оба SKU.
        self.assertEqual(req.call_count, 1)
        method, url = req.call_args.args[0], req.call_args.args[1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/merchant/process/process/batch"))
        body = req.call_args.kwargs["json"]
        self.assertIsInstance(body, list)  # корневой массив, не {"offers": [...]}
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["availabilities"][0]["storeId"], "999_001")
        self.assertEqual(self._row().pushed_price, 900)
        self.assertIsNone(self._row().desired_at)

    def test_quota_exhaustion_defers_instead_of_losing_intents(self):
        self._seed_pushed(price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-1", price=900, stock=10, priority=Priority.LOSING)
        with patch("backend.state_helpers._mc_api_request") as req, \
             patch.object(offer_sync.quota, "get",
                          return_value=QuotaState(limit=250, used=250, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        req.assert_not_called()
        self.assertEqual(stats["deferred"], 1)
        # Намерение НЕ потеряно — уйдёт после сброса окна.
        self.assertIsNotNone(self._row().desired_at)
        self.assertEqual(self._row().desired_price, 900)

    def test_p0_still_passes_when_ordinary_writes_are_cut_off(self):
        self._seed_pushed(price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-1", stock=0, available=False, priority=Priority.WITHDRAW)
        with patch("backend.state_helpers._mc_api_request", return_value=self._mock_ok_batch(1)), \
             patch.object(offer_sync.quota, "get",
                          return_value=QuotaState(limit=250, used=240, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        self.assertEqual(stats["written"], 1)

    def test_429_keeps_intents_and_marks_window_exhausted(self):
        self._seed_pushed(price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-1", price=900, stock=10)
        blocked = MagicMock()
        blocked.status_code = 429
        blocked.text = "Too Many Requests"
        with patch("backend.state_helpers._mc_api_request", return_value=blocked), \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)), \
             patch.object(offer_sync.quota, "mark_exhausted",
                          return_value=QuotaState(limit=250, used=250, known=True)) as marked:
            stats = offer_sync.flush_store("s1", "999")
        marked.assert_called_once()
        self.assertEqual(stats["written"], 0)
        self.assertIsNotNone(self._row().desired_at)

    def test_unverifiable_intent_is_deferred_not_dropped(self):
        # Намерение только по остатку, базы нет, а сверка не удалась. Раньше
        # такое молча гасилось: веер Мастер-Группы, не попавший в бюджет
        # сверок, терял запись насовсем.
        offer_sync.enqueue("s1", "SKU-NEW", stock=7, reason="group:g1")
        failed = MagicMock()
        failed.status_code = 500
        failed.text = "boom"
        with patch("backend.state_helpers._mc_api_request", return_value=failed), \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        self.assertEqual(stats["written"], 0)
        self.assertEqual(stats["no_op"], 0)
        self.assertEqual(stats["deferred"], 1)
        row = self._row("SKU-NEW")
        self.assertIsNotNone(row.desired_at)
        self.assertEqual(row.desired_stock, 7)

    def test_verification_finds_the_exact_sku_among_prefix_matches(self):
        # t= у Kaspi — поиск по подстроке: "H422-0002" возвращает и
        # "H422-0002-3", и "H422-0002-7". При l=1 точного совпадения в выдаче
        # не оказывалось и намерение висело в очереди вечно.
        offer_sync.enqueue("s1", "H422-0002", stock=994, reason="group:g1")
        listing = MagicMock()
        listing.status_code = 200
        listing.json.return_value = {"data": [
            {"sku": "H422-0002-3", "price": 741, "masterSku": "M3",
             "availabilities": [{"available": "yes", "storeId": "999_001", "stockCount": 998}]},
            {"sku": "H422-0002-7", "price": 499, "masterSku": "M7",
             "availabilities": [{"available": "yes", "storeId": "999_001", "stockCount": 998}]},
            {"sku": "H422-0002", "price": 990, "masterSku": "M0", "title": "Стойка",
             "availabilities": [{"available": "yes", "storeId": "999_001", "stockCount": 1011}]},
        ]}

        def route(method, url, **kwargs):
            if method == "GET":
                self.assertIn("l=50", url)
                return listing
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = [{"id": "tx0"}]
            return resp

        with patch("backend.state_helpers._mc_api_request", side_effect=route) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        self.assertEqual(stats["verified"], 1)
        self.assertEqual(stats["written"], 1)
        body = req.call_args.kwargs["json"]
        self.assertEqual(body[0]["sku"], "H422-0002")
        self.assertEqual(body[0]["cityPrices"][0]["value"], 990)

    def test_missing_baseline_is_fetched_then_written(self):
        offer_sync.enqueue("s1", "SKU-NEW", stock=7, reason="group:g1")
        listing = MagicMock()
        listing.status_code = 200
        listing.json.return_value = {"data": [{
            "sku": "SKU-NEW", "price": 1500, "masterSku": "M9", "title": "Товар",
            "availabilities": [{"available": "yes", "storeId": "999_007", "stockCount": 40}],
        }]}

        def route(method, url, **kwargs):
            return listing if method == "GET" else self._mock_ok_batch(1)

        with patch("backend.state_helpers._mc_api_request", side_effect=route) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        self.assertEqual(stats["verified"], 1)
        self.assertEqual(stats["written"], 1)
        body = req.call_args.kwargs["json"]
        # Цену подставили фактическую с Kaspi, а точку продаж — из её ответа.
        self.assertEqual(body[0]["cityPrices"][0]["value"], 1500)
        self.assertEqual(body[0]["availabilities"][0]["storeId"], "999_007")
        self.assertEqual(body[0]["availabilities"][0]["stockCount"], 7)

    def test_stock_only_intent_keeps_existing_price(self):
        # В payload Kaspi цена и остаток идут вместе — при намерении только по
        # остатку цену обязаны подставить текущую, а не обнулить.
        self._seed_pushed(price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-1", stock=3)
        with patch("backend.state_helpers._mc_api_request", return_value=self._mock_ok_batch(1)) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            offer_sync.flush_store("s1", "999")
        body = req.call_args.kwargs["json"]
        self.assertEqual(body[0]["cityPrices"][0]["value"], 1000)
        self.assertEqual(body[0]["availabilities"][0]["stockCount"], 3)

    def test_zero_stock_marks_offer_unavailable(self):
        self._seed_pushed(price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-1", stock=0, available=False, priority=Priority.WITHDRAW)
        with patch("backend.state_helpers._mc_api_request", return_value=self._mock_ok_batch(1)) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            offer_sync.flush_store("s1", "999")
        self.assertEqual(req.call_args.kwargs["json"][0]["availabilities"][0]["available"], "no")

    def test_p0_is_written_before_lower_priorities(self):
        for i in range(3):
            self._seed_pushed(sku=f"SKU-{i}", price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-0", price=900, priority=Priority.MINOR)
        offer_sync.enqueue("s1", "SKU-1", price=900, priority=Priority.LOSING)
        offer_sync.enqueue("s1", "SKU-2", stock=0, available=False, priority=Priority.WITHDRAW)
        with patch("backend.state_helpers._mc_api_request", return_value=self._mock_ok_batch(3)) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            offer_sync.flush_store("s1", "999")
        order = [o["sku"] for o in req.call_args.kwargs["json"]]
        self.assertEqual(order[0], "SKU-2")
        self.assertEqual(order[1], "SKU-1")


class TestQuotaHeartbeatOnIdleStore(OfferSyncDBTestCase):
    """
    Регрессия 2026-08-12: на сторе без активного авто-демпинга (все SKU
    отключены, группы не шевелятся) очередь намерений пуста — flush_store
    раньше уходил в ранний return, ни разу не вызвав quota.get(), кэш
    quota.peek() никогда не наполнялся, и чип "Лимит Kaspi" на вкладке
    демпинга не появлялся вообще, сколько ни жди.
    """

    def test_quota_is_fetched_even_with_an_empty_queue(self):
        with patch.object(offer_sync.quota, "peek", return_value=None) as peek, \
             patch.object(offer_sync.quota, "get",
                          return_value=QuotaState(limit=250, used=10, known=True)) as get:
            stats = offer_sync.flush_store("s1", "999")
        peek.assert_called_once_with("s1")
        get.assert_called_once_with("s1", "999")
        self.assertEqual(stats["candidates"], 0)

    def test_quota_is_not_refetched_within_the_heartbeat_window(self):
        fresh = QuotaState(limit=250, used=10, known=True, fetched_at=datetime.datetime.now())
        with patch.object(offer_sync.quota, "peek", return_value=fresh), \
             patch.object(offer_sync.quota, "get") as get:
            offer_sync.flush_store("s1", "999")
        get.assert_not_called()

    def test_quota_is_refetched_once_the_snapshot_goes_stale(self):
        stale = QuotaState(
            limit=250, used=10, known=True,
            fetched_at=datetime.datetime.now() - datetime.timedelta(
                seconds=offer_sync.QUOTA_HEARTBEAT_SECONDS + 1),
        )
        with patch.object(offer_sync.quota, "peek", return_value=stale), \
             patch.object(offer_sync.quota, "get",
                          return_value=QuotaState(limit=250, used=20, known=True)) as get:
            offer_sync.flush_store("s1", "999")
        get.assert_called_once_with("s1", "999")


class TestUnconfirmedWriteIsRechecked(OfferSyncDBTestCase):
    """Регрессия 2026-08-13: ручка batch асинхронная, 200 = "принято", не
    "применено". Батч из 105 офферов ушёл в 19:10 с 200 и без ошибок, но часть
    строк Kaspi не применил (X471-0018-6: у нас 471 ₸, на карточке 720 ₸).
    Мы копировали отправленное значение в фактическое состояние, и позиция
    залипала навсегда: демпер решает, флашер гасит вхолостую, цена старая.
    """

    def _seed_unconfirmed(self, sku="SKU-1", pushed=471, age_seconds=1000):
        from backend.db.models import KaspiOfferSyncDB
        from sqlalchemy.orm import Session
        pushed_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=age_seconds)
        with Session(self._engine) as s:
            s.add(KaspiOfferSyncDB(
                store_id="s1", sku=sku, master_sku="M1", model="Товар",
                store_suffix="001", city_id="750000000",
                pushed_price=pushed, pushed_stock=10, pushed_at=pushed_at,
                kaspi_price=None, kaspi_stock=None, kaspi_seen_at=None,
                priority=Priority.MINOR,
            ))
            s.commit()

    def _listing(self, price):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": [{
            "sku": "SKU-1", "price": price, "masterSku": "M1", "title": "Товар",
            "availabilities": [{"available": "yes", "storeId": "999_001", "stockCount": 10}],
        }]}
        return resp

    def test_repeat_of_unapplied_price_is_verified_and_resent(self):
        self._seed_unconfirmed(pushed=471)
        offer_sync.enqueue("s1", "SKU-1", price=471, stock=10)

        batch = MagicMock(status_code=200)
        batch.json.return_value = [{"id": "tx0"}]

        def route(method, url, **kwargs):
            return self._listing(720) if method == "GET" else batch

        with patch("backend.state_helpers._mc_api_request", side_effect=route) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")

        self.assertEqual(stats["verified"], 1)
        self.assertEqual(stats["written"], 1)
        self.assertEqual(stats["no_op"], 0)
        body = req.call_args.kwargs["json"]
        self.assertEqual(body[0]["cityPrices"][0]["value"], 471)

    def test_repeat_of_applied_price_is_dropped_after_verification(self):
        self._seed_unconfirmed(pushed=471)
        offer_sync.enqueue("s1", "SKU-1", price=471, stock=10)

        with patch("backend.state_helpers._mc_api_request", return_value=self._listing(471)), \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")

        self.assertEqual(stats["verified"], 1)
        self.assertEqual(stats["no_op"], 1)
        self.assertEqual(stats["written"], 0)

    def test_recent_write_is_not_rechecked(self):
        # Сразу после записи Kaspi ещё обрабатывает батч — ходить незачем.
        self._seed_unconfirmed(pushed=471, age_seconds=60)
        offer_sync.enqueue("s1", "SKU-1", price=471, stock=10)

        with patch("backend.state_helpers._mc_api_request") as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")

        req.assert_not_called()
        self.assertEqual(stats["no_op"], 1)

    def test_write_does_not_claim_kaspi_state_as_seen(self):
        self._seed_unconfirmed(pushed=1000, age_seconds=60)
        offer_sync.enqueue("s1", "SKU-1", price=900, stock=10)

        batch = MagicMock(status_code=200)
        batch.json.return_value = [{"id": "tx0"}]
        with patch("backend.state_helpers._mc_api_request", return_value=batch), \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            offer_sync.flush_store("s1", "999")

        row = self._row()
        self.assertEqual(row.pushed_price, 900)
        self.assertIsNone(row.kaspi_price)
        self.assertIsNone(row.kaspi_seen_at)


class TestNeverWriteZeroPrice(OfferSyncDBTestCase):
    """
    Регрессия на инцидент 2026-08-12: карточки H422-0002-7 и H422-0002-9 были
    сняты Kaspi с продажи с причиной "не указан в прайс-листе", потому что мы
    записали им цену 0.

    Причина — плоское поле offer["price"] у bff/offer-view/list: у ~35%
    офферов это устаревший кэш с нулём при реально назначенной цене (правило
    уже было выучено в kaspi_wrapper, но _fetch_kaspi_state читала поле
    наивно). Намерение по остатку от веера Мастер-Группы подставляло эту
    "базу" в cityPrices — и обнуляло цену живого товара.
    """

    def _listing(self, offer: dict):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": [offer]}
        return resp

    def test_real_price_is_recovered_when_flat_field_is_a_stale_zero(self):
        offer_sync.enqueue("s1", "SKU-NEW", stock=7, reason="group:g1")
        listing = self._listing({
            "sku": "SKU-NEW", "price": 0, "masterSku": "M9", "title": "Товар",
            "cityPrices": [{"cityId": "750000000", "value": 992}],
            "minPrice": 992, "rangePrice": {"MIN": 992, "MAX": 992},
            "availabilities": [{"available": "yes", "storeId": "999_001", "stockCount": 27}],
        })

        def route(method, url, **kwargs):
            return listing if method == "GET" else self._mock_batch_ok()

        with patch("backend.state_helpers._mc_api_request", side_effect=route) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        self.assertEqual(stats["written"], 1)
        self.assertEqual(req.call_args.kwargs["json"][0]["cityPrices"][0]["value"], 992)

    def test_unreadable_price_defers_instead_of_writing_zero(self):
        # Цены нет вообще ни в одном поле — писать нечего. Раньше сюда
        # приезжал ноль и снимал карточку с продажи.
        offer_sync.enqueue("s1", "SKU-NEW", stock=7, reason="group:g1")
        listing = self._listing({
            "sku": "SKU-NEW", "price": 0, "masterSku": "M9", "title": "Товар",
            "availabilities": [{"available": "yes", "storeId": "999_001", "stockCount": 27}],
        })
        with patch("backend.state_helpers._mc_api_request", return_value=listing) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        self.assertEqual(stats["written"], 0)
        self.assertEqual(stats["deferred"], 1)
        # Ни одного POST — только чтение сверки.
        self.assertTrue(all(c.args[0] == "GET" for c in req.call_args_list))
        # Намерение живо и уйдёт, когда цена прочитается.
        self.assertIsNotNone(self._row("SKU-NEW").desired_at)

    def test_zero_baseline_from_history_never_reaches_the_wire(self):
        # Последний рубеж: даже если ноль просочился в базу (как это уже
        # произошло на проде до фикса), отправлять его нельзя.
        self._seed_pushed(price=0, stock=994)
        offer_sync.enqueue("s1", "SKU-1", stock=500)
        with patch("backend.state_helpers._mc_api_request") as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        posts = [c for c in req.call_args_list if c.args and c.args[0] == "POST"]
        self.assertEqual(posts, [])
        self.assertEqual(stats["written"], 0)

    def test_withdrawal_still_works_with_a_valid_price(self):
        # Снятие с продажи — это остаток 0 при живой цене, а не цена 0.
        self._seed_pushed(price=1000, stock=10)
        offer_sync.enqueue("s1", "SKU-1", stock=0, available=False, priority=Priority.WITHDRAW)
        with patch("backend.state_helpers._mc_api_request", return_value=self._mock_batch_ok()) as req, \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            stats = offer_sync.flush_store("s1", "999")
        self.assertEqual(stats["written"], 1)
        body = req.call_args.kwargs["json"]
        self.assertEqual(body[0]["cityPrices"][0]["value"], 1000)
        self.assertEqual(body[0]["availabilities"][0]["available"], "no")

    def _mock_batch_ok(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [{"id": "tx0"}]
        return resp


class TestAntiFlap(OfferSyncDBTestCase):
    def test_ping_pong_freezes_the_sku(self):
        # Два бота, пилящие друг друга по цене, — самая дорогая патология:
        # они вдвоём способны выбрать всё окно, не заработав ничего.
        self._seed_pushed(price=1000, stock=10)
        prices = [990, 1000, 990, 1000, 990]
        with patch("backend.state_helpers._mc_api_request", return_value=self._mock_ok_batch(1)), \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            for p in prices:
                offer_sync.enqueue("s1", "SKU-1", price=p, priority=Priority.LOSING)
                offer_sync.flush_store("s1", "999")
        frozen = offer_sync._flap_frozen("s1", "SKU-1", datetime.datetime.now().timestamp())
        self.assertTrue(frozen)

    def test_steady_direction_is_not_frozen(self):
        self._seed_pushed(price=1000, stock=10)
        with patch("backend.state_helpers._mc_api_request", return_value=self._mock_ok_batch(1)), \
             patch.object(offer_sync.quota, "get", return_value=QuotaState(limit=250, used=0, known=True)):
            for p in (990, 980, 970, 960, 950):
                offer_sync.enqueue("s1", "SKU-1", price=p, priority=Priority.LOSING)
                offer_sync.flush_store("s1", "999")
        self.assertFalse(offer_sync._flap_frozen("s1", "SKU-1", datetime.datetime.now().timestamp()))

    def _mock_ok_batch(self, ids=1):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [{"id": f"tx{i}"} for i in range(ids)]
        return resp


if __name__ == "__main__":
    unittest.main()


class TestCycleUsesDbMerchantId(unittest.IsolatedAsyncioTestCase):
    """
    Флашер тикает раз в 45 секунд на каждый стор. Резолвить merchant_id через
    Kaspi на каждом тике нельзя: discover_merchant_id ходит в
    /pointofservices (на проде стабильно 500) и дальше в историю заказов —
    это ровно тот постоянный лишний трафик, за который IP VPS уже банили
    (HANDOFF 3.8/3.9). Берём из Store.merchant_id.
    """

    async def test_merchant_id_comes_from_db_without_touching_kaspi(self):
        from types import SimpleNamespace

        store = SimpleNamespace(store_id="s1", token="tok", merchant_id="20186986")
        with patch("backend.security.store_context.list_active_stores", return_value=[store]), \
             patch("backend.dumper.config.discover_merchant_id") as discover, \
             patch.object(offer_sync, "flush_store", return_value={"written": 0}) as flush:
            await offer_sync.run_offer_sync_cycle()
        discover.assert_not_called()
        flush.assert_called_once_with("s1", "20186986")

    async def test_store_without_numeric_merchant_id_is_skipped(self):
        from types import SimpleNamespace

        store = SimpleNamespace(store_id="s1", token="tok", merchant_id="m_deadbeef")
        with patch("backend.security.store_context.list_active_stores", return_value=[store]), \
             patch.object(offer_sync, "flush_store") as flush:
            await offer_sync.run_offer_sync_cycle()
        flush.assert_not_called()


class TestQuotaPeekForUI(unittest.TestCase):
    """
    Индикатор на вкладке демпинга поллит /api/dumping/status раз в 10 секунд.
    peek() обязан отдавать только кэш: живой запрос к Kaspi отсюда дал бы 360
    обращений в час на каждую открытую вкладку — тот самый паттерн, что уже
    приводил к бану IP этого VPS (HANDOFF 3.8).
    """

    def test_peek_never_calls_kaspi(self):
        q = KaspiOfferQuota()
        with patch.object(q, "_fetch", side_effect=AssertionError("peek не должен ходить в сеть")):
            self.assertIsNone(q.peek("store-1"))

    def test_peek_returns_cached_snapshot(self):
        q = KaspiOfferQuota()
        future = datetime.datetime.now() + datetime.timedelta(minutes=5)
        q._states["store-1"] = QuotaState(limit=250, used=46, reset_at=future,
                                          fetched_at=datetime.datetime.now(), known=True)
        snap = q.peek("store-1")
        self.assertEqual(snap.used, 46)
        self.assertEqual(snap.remaining, 204)

    def test_peek_hides_a_stale_snapshot(self):
        # Флашер обновляет кэш раз в 45с. Если снимок протух — писатель встал,
        # и честнее не показать ничего, чем залипшие цифры.
        q = KaspiOfferQuota()
        q._states["store-1"] = QuotaState(
            limit=250, used=250, reset_at=None,
            fetched_at=datetime.datetime.now() - datetime.timedelta(minutes=10), known=True,
        )
        self.assertIsNone(q.peek("store-1"))


class TestResetTimeTimezone(unittest.TestCase):
    """
    Kaspi отдаёт expirationDate в алматинском времени (UTC+5) БЕЗ смещения, а
    прод-сервер живёт в UTC. Наблюдалось живьём: 18394 секунды до сброса при
    длине окна в 30 минут.
    """

    def test_window_length_is_parsed(self):
        self.assertEqual(_parse_window_seconds("30m"), 1800.0)
        self.assertEqual(_parse_window_seconds("2h"), 7200.0)
        self.assertEqual(_parse_window_seconds(None), 1800.0)
        self.assertEqual(_parse_window_seconds("непонятно"), 1800.0)

    def test_five_hour_skew_is_normalized_to_the_window(self):
        now = datetime.datetime(2026, 8, 12, 13, 28, 0)          # UTC
        reset_almaty = datetime.datetime(2026, 8, 12, 18, 34, 36)  # тот же момент +5ч
        state = QuotaState(reset_at=reset_almaty, window_seconds=1800.0)
        self.assertAlmostEqual(state.seconds_until_reset(now), 396.0, delta=2)

    def test_no_skew_passes_through_untouched(self):
        now = datetime.datetime(2026, 8, 12, 13, 28, 0)
        state = QuotaState(reset_at=now + datetime.timedelta(minutes=7), window_seconds=1800.0)
        self.assertAlmostEqual(state.seconds_until_reset(now), 420.0, delta=1)

    def test_past_reset_is_zero(self):
        now = datetime.datetime(2026, 8, 12, 13, 28, 0)
        state = QuotaState(reset_at=now - datetime.timedelta(minutes=1))
        self.assertEqual(state.seconds_until_reset(now), 0.0)

    def test_window_length_survives_parsing(self):
        state = _parse_quota({
            "limitTime": "30m", "limits": {"OFFER": 250},
            "statistics": [{"documentType": "OFFER", "uploadedCount": 1,
                            "expirationDate": "2026-08-12T18:34:36.000"}],
        })
        self.assertEqual(state.window_seconds, 1800.0)
