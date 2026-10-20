"""Кэш экранных списков «Упаковка»/«Передача» (KaspiAPI._cached_view):
stale-while-revalidate, разделение по токену магазина, сброс после действий
над заказами и защита от записи устаревшего обхода после сброса. Плюс общий
keep-alive пул Kaspi не хранит куки (он один на все магазины)."""

import os
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from backend.kaspi_wrapper import KaspiAPI, _http  # noqa: E402


class KaspiViewCacheTest(unittest.TestCase):
    def setUp(self):
        self._reset()
        self.clock = 1000.0
        self._time = patch("backend.kaspi_wrapper.time.time", side_effect=lambda: self.clock)
        self._time.start()

    def tearDown(self):
        self._time.stop()
        self._reset()

    @staticmethod
    def _reset():
        KaspiAPI._view_cache.clear()
        KaspiAPI._view_refreshing.clear()
        KaspiAPI._view_gen.clear()

    @staticmethod
    def _wait_refresh():
        for t in threading.enumerate():
            if t.name == "kaspi-view-refresh":
                t.join(timeout=5)

    def test_fresh_entry_is_served_without_refetch(self):
        with patch.object(KaspiAPI, "get_transfers", return_value=[{"id": "1"}]) as fetch:
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-a"), [{"id": "1"}])
            self.clock += KaspiAPI._VIEW_TTL_SECONDS - 1
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-a"), [{"id": "1"}])
        self.assertEqual(fetch.call_count, 1)

    def test_stale_entry_is_served_at_once_and_refreshed_in_background(self):
        with patch.object(KaspiAPI, "get_transfers", side_effect=[["old"], ["new"]]) as fetch:
            KaspiAPI.get_transfers_cached("tok-a")
            self.clock += KaspiAPI._VIEW_TTL_SECONDS + 1
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-a"), ["old"])
            self._wait_refresh()
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-a"), ["new"])
        self.assertEqual(fetch.call_count, 2)

    def test_too_old_entry_is_refetched_synchronously(self):
        with patch.object(KaspiAPI, "get_transfers", side_effect=[["old"], ["new"]]):
            KaspiAPI.get_transfers_cached("tok-a")
            self.clock += KaspiAPI._VIEW_MAX_STALE_SECONDS + 1
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-a"), ["new"])

    def test_stores_and_filters_do_not_share_entries(self):
        def fake(token, delivery_filter="ALL"):
            return [f"{token}:{delivery_filter}"]
        with patch.object(KaspiAPI, "get_transfers", side_effect=fake):
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-a"), ["tok-a:ALL"])
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-b"), ["tok-b:ALL"])
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-a", "EXPRESS"), ["tok-a:EXPRESS"])

    def test_orders_key_includes_query_params(self):
        with patch.object(KaspiAPI, "get_orders_with_products",
                          side_effect=lambda token, **kw: [kw["state"]]) as fetch:
            self.assertEqual(KaspiAPI.get_orders_with_products_cached("tok-a", state="NEW", limit=20), ["NEW"])
            self.assertEqual(KaspiAPI.get_orders_with_products_cached("tok-a", state="KASPI_DELIVERY", limit=20),
                             ["KASPI_DELIVERY"])
            self.assertEqual(KaspiAPI.get_orders_with_products_cached("tok-a", limit=20, state="NEW"), ["NEW"])
        self.assertEqual(fetch.call_count, 2)

    def test_invalidate_drops_only_that_store(self):
        with patch.object(KaspiAPI, "get_transfers",
                          side_effect=lambda t, delivery_filter="ALL": [t]) as fetch:
            KaspiAPI.get_transfers_cached("tok-a")
            KaspiAPI.get_transfers_cached("tok-b")
            KaspiAPI.invalidate_order_views("tok-a")
            KaspiAPI.get_transfers_cached("tok-a")
            KaspiAPI.get_transfers_cached("tok-b")
        self.assertEqual(fetch.call_count, 3)

    def test_fetch_started_before_invalidation_is_not_stored(self):
        """Фоновый обход начался, пока заказ упаковывали — его результат
        старше сброса и не должен вернуться в кэш."""
        started, release = threading.Event(), threading.Event()

        def slow_old(token, delivery_filter="ALL"):
            started.set()
            release.wait(5)
            return ["before-pack"]

        with patch.object(KaspiAPI, "get_transfers", return_value=["v1"]):
            KaspiAPI.get_transfers_cached("tok-a")
        self.clock += KaspiAPI._VIEW_TTL_SECONDS + 1
        with patch.object(KaspiAPI, "get_transfers", side_effect=slow_old):
            self.assertEqual(KaspiAPI.get_transfers_cached("tok-a"), ["v1"])
            started.wait(5)
            KaspiAPI.invalidate_order_views("tok-a")
            release.set()
            self._wait_refresh()
        self.assertNotIn(("tok-a", "transfers", "ALL"), KaspiAPI._view_cache)

    def test_update_order_status_invalidates(self):
        class Resp:
            def raise_for_status(self):
                pass
        with patch.object(KaspiAPI, "get_transfers", return_value=["x"]):
            KaspiAPI.get_transfers_cached("tok-a")
        with patch.object(_http, "post", return_value=Resp()):
            KaspiAPI.update_order_status("tok-a", "int-1", "ACCEPTED_BY_MERCHANT")
        self.assertEqual(KaspiAPI._view_cache, {})


class KaspiHttpSessionTest(unittest.TestCase):
    def test_shared_session_rejects_cookies(self):
        """Пул общий для всех магазинов — кука одного не должна уехать другому."""
        import requests
        from http.cookiejar import Cookie
        cookie = Cookie(0, "waf", "x", None, False, "kaspi.kz", True, False, "/", True,
                        True, None, False, None, None, {})
        request = requests.cookies.MockRequest(
            requests.Request("GET", "https://kaspi.kz/shop/api/v2/orders").prepare()
        )
        self.assertFalse(_http.cookies._policy.set_ok(cookie, request))


if __name__ == "__main__":
    unittest.main()
