import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from backend.kaspi_wrapper import KaspiAPI


class TestPackagingTabsCache(unittest.TestCase):
    """
    Категория 1 (B.2): get_all_packaging_tabs_cached() is the single shared
    cache now used by routers/orders.py, routers/stock_groups.py, and the
    30s packaging tick in stock_sync_worker.py deliberately still bypasses
    it (see backend/services/stock_sync_worker.py::process_packaging_auto_deduction
    — that tick needs fresher data than a 45s shared cache would give it).
    """

    def setUp(self):
        KaspiAPI._packaging_tabs_cache.clear()
        KaspiAPI._packaging_tabs_refresh_locks.clear()

    @patch("backend.kaspi_wrapper.KaspiAPI.get_all_packaging_tabs")
    def test_second_call_within_ttl_uses_cache(self, mock_tabs):
        mock_tabs.return_value = {"cargo_assembly": [1]}
        KaspiAPI.get_all_packaging_tabs_cached("token-A")
        KaspiAPI.get_all_packaging_tabs_cached("token-A")
        mock_tabs.assert_called_once()

    @patch("backend.kaspi_wrapper.KaspiAPI.get_all_packaging_tabs")
    def test_different_tokens_do_not_share_cache_entries(self, mock_tabs):
        mock_tabs.side_effect = [
            {"cargo_assembly": ["store-A order"]},
            {"cargo_assembly": ["store-B order"]},
        ]
        result_a = KaspiAPI.get_all_packaging_tabs_cached("token-A")
        result_b = KaspiAPI.get_all_packaging_tabs_cached("token-B")
        self.assertEqual(mock_tabs.call_count, 2)
        self.assertEqual(result_a["cargo_assembly"], ["store-A order"])
        self.assertEqual(result_b["cargo_assembly"], ["store-B order"])

    @patch("backend.kaspi_wrapper.KaspiAPI.get_all_packaging_tabs")
    def test_expired_ttl_refetches(self, mock_tabs):
        mock_tabs.return_value = {"cargo_assembly": []}
        KaspiAPI.get_all_packaging_tabs_cached("token-A", ttl_seconds=0.0)
        KaspiAPI.get_all_packaging_tabs_cached("token-A", ttl_seconds=0.0)
        self.assertEqual(mock_tabs.call_count, 2)

    @patch("backend.kaspi_wrapper.KaspiAPI.get_all_packaging_tabs")
    def test_exception_falls_back_to_stale_cache(self, mock_tabs):
        mock_tabs.side_effect = [{"cargo_assembly": ["stale"]}, Exception("network error")]
        KaspiAPI.get_all_packaging_tabs_cached("token-A", ttl_seconds=0.0)
        result = KaspiAPI.get_all_packaging_tabs_cached("token-A", ttl_seconds=0.0)
        self.assertEqual(result["cargo_assembly"], ["stale"])

    @patch("backend.kaspi_wrapper.KaspiAPI.get_all_packaging_tabs")
    def test_exception_with_no_prior_cache_returns_empty_dict(self, mock_tabs):
        mock_tabs.side_effect = Exception("network error")
        result = KaspiAPI.get_all_packaging_tabs_cached("token-never-seen")
        self.assertEqual(result, {})


class TestPackagingTabsSingleFlight(unittest.TestCase):
    """
    Кэш держал TTL, но не защищал сам промах: в момент истечения TTL все
    пришедшие одновременно запросы промахивались вместе и каждый запускал
    свой полный 13-дневный обход (до 185с на проде). Отсюда p95=27с у
    `GET /api/stock-groups` при p50=1.6с.
    """

    def setUp(self):
        KaspiAPI._packaging_tabs_cache.clear()
        KaspiAPI._packaging_tabs_refresh_locks.clear()

    def test_concurrent_cold_misses_trigger_exactly_one_fetch(self):
        inside_fetch = threading.Event()
        let_fetch_finish = threading.Event()
        fetch_calls = []

        def slow_fetch(token):
            fetch_calls.append(token)
            inside_fetch.set()
            let_fetch_finish.wait(10)
            return {"cargo_assembly": ["fresh"]}

        results = {}

        def worker(i):
            results[i] = KaspiAPI.get_all_packaging_tabs_cached("token-A")

        with patch.object(KaspiAPI, "get_all_packaging_tabs", side_effect=slow_fetch):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
            threads[0].start()
            # Дожидаемся, пока первый поток реально окажется ВНУТРИ обхода —
            # иначе остальные могут стартовать раньше и тест проверит гонку,
            # а не single-flight.
            self.assertTrue(inside_fetch.wait(10))
            for t in threads[1:]:
                t.start()
            time.sleep(0.2)  # дать остальным дойти до лока
            let_fetch_finish.set()
            for t in threads:
                t.join(10)

        self.assertEqual(len(fetch_calls), 1, f"обход ушёл в Kaspi {len(fetch_calls)} раз вместо одного")
        for i in range(4):
            self.assertEqual(results[i]["cargo_assembly"], ["fresh"])

    def test_stale_data_served_instantly_while_refresh_in_flight(self):
        with patch.object(KaspiAPI, "get_all_packaging_tabs", return_value={"cargo_assembly": ["stale"]}):
            KaspiAPI.get_all_packaging_tabs_cached("token-A", ttl_seconds=0.0)

        inside_fetch = threading.Event()
        let_fetch_finish = threading.Event()

        def slow_fetch(token):
            inside_fetch.set()
            let_fetch_finish.wait(10)
            return {"cargo_assembly": ["fresh"]}

        with patch.object(KaspiAPI, "get_all_packaging_tabs", side_effect=slow_fetch):
            refresher = threading.Thread(
                target=KaspiAPI.get_all_packaging_tabs_cached, args=("token-A",), kwargs={"ttl_seconds": 0.0}
            )
            refresher.start()
            self.assertTrue(inside_fetch.wait(10))

            began = time.monotonic()
            result = KaspiAPI.get_all_packaging_tabs_cached("token-A", ttl_seconds=0.0)
            elapsed = time.monotonic() - began

            let_fetch_finish.set()
            refresher.join(10)

        self.assertEqual(result["cargo_assembly"], ["stale"])
        self.assertLess(elapsed, 1.0, "протухшие данные должны отдаваться сразу, а не ждать чужой обход")


if __name__ == "__main__":
    unittest.main()
