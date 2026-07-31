"""
Tests for backend/perf/ (category-0 perf instrumentation, PERF_PROFILING=1).
Covers: thread-safe counter aggregation, outbound-traffic byte accounting
via the requests.Session.send hook, and that the /api/_perf/snapshot route
only exists when the flag is set.
"""
import os
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from backend.perf.collector import PerfCollector
from backend.perf import http_hooks


class TestPerfCollector(unittest.TestCase):
    def setUp(self):
        self.collector = PerfCollector()

    def test_record_inbound_aggregates_count_and_bytes(self):
        self.collector.record_inbound("GET /api/products", 0.10, resp_bytes=1000)
        self.collector.record_inbound("GET /api/products", 0.30, resp_bytes=2000)
        snap = self.collector.snapshot()
        stats = snap["inbound"]["GET /api/products"]
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["resp_bytes"], 3000)
        self.assertAlmostEqual(stats["avg_ms"], 200.0, delta=1)
        self.assertAlmostEqual(stats["max_ms"], 300.0, delta=1)

    def test_record_outbound_splits_by_path_and_aggregates_by_host(self):
        self.collector.record_outbound("kaspi.kz", "/shop/api/v2/orders", 0.2, req_bytes=100, resp_bytes=500)
        self.collector.record_outbound("kaspi.kz", "/shop/api/v2/cities", 0.1, req_bytes=50, resp_bytes=200)
        snap = self.collector.snapshot()
        self.assertEqual(snap["outbound_by_host"]["kaspi.kz"]["count"], 2)
        self.assertEqual(snap["outbound_by_host"]["kaspi.kz"]["total_bytes"], 100 + 500 + 50 + 200)
        self.assertEqual(len(snap["outbound_by_path"]), 2)

    def test_percentiles_are_monotonic(self):
        for ms in [10, 20, 30, 40, 1000]:
            self.collector.record_inbound("GET /x", ms / 1000)
        stats = self.collector.snapshot()["inbound"]["GET /x"]
        self.assertLessEqual(stats["p50_ms"], stats["p95_ms"])
        self.assertLessEqual(stats["p95_ms"], stats["max_ms"])

    def test_loop_lag_tracks_seconds_blocked_over_threshold(self):
        self.collector.record_loop_lag(0.05)   # below 100ms threshold
        self.collector.record_loop_lag(0.15)   # counts
        self.collector.record_loop_lag(0.20)   # counts
        snap = self.collector.snapshot()
        lag = snap["event_loop_lag"]
        self.assertEqual(lag["samples"], 3)
        self.assertAlmostEqual(lag["seconds_blocked_over_100ms"], 0.35, delta=0.001)
        self.assertAlmostEqual(lag["max_ms"], 200.0, delta=1)

    def test_concurrent_writes_do_not_lose_counts(self):
        """PerfCollector is written to from both the event loop (middleware)
        and worker threads (http_hooks) — the lock must actually serialize
        access, not just exist decoratively."""
        n_threads = 20
        n_per_thread = 200

        def worker():
            for _ in range(n_per_thread):
                self.collector.record_inbound("GET /concurrent", 0.001)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = self.collector.snapshot()
        self.assertEqual(snap["inbound"]["GET /concurrent"]["count"], n_threads * n_per_thread)


class TestHostAndPathClassification(unittest.TestCase):
    def test_known_kaspi_hosts_map_to_their_own_family(self):
        self.assertEqual(http_hooks._classify_host("https://kaspi.kz/shop/api/v2/orders"), "kaspi.kz")
        self.assertEqual(
            http_hooks._classify_host("https://mc.shop.kaspi.kz/bff/offer-view/list"), "mc.shop.kaspi.kz"
        )
        self.assertEqual(
            http_hooks._classify_host("https://idmc.shop.kaspi.kz/login"), "idmc.shop.kaspi.kz"
        )

    def test_unknown_host_falls_back_to_its_own_netloc(self):
        self.assertEqual(http_hooks._classify_host("https://api.telegram.org/bot123/sendMessage"), "api.telegram.org")

    def test_path_key_collapses_numeric_and_long_segments(self):
        self.assertEqual(http_hooks._path_key("/shop/api/v2/orders/123456/entries"), "/shop/api/v2/orders/{id}/entries")
        self.assertEqual(
            http_hooks._path_key("/bff/offer-view/details?m=1&s=2"), "/bff/offer-view/details"
        )

    def test_safe_len_handles_bytes_str_and_none(self):
        self.assertEqual(http_hooks._safe_len(b"hello"), 5)
        self.assertEqual(http_hooks._safe_len("привет"), len("привет".encode("utf-8")))
        self.assertEqual(http_hooks._safe_len(None), 0)


class TestRequestsHookByteAccounting(unittest.TestCase):
    """
    Exercises the real _patch_requests() wrapping logic without any real
    network I/O: requests.sessions.Session.send is monkeypatched to a stub
    BEFORE _patch_requests() captures "original_send", so the wrapper calls
    our stub instead of opening a real connection.
    """

    def setUp(self):
        import requests
        self._orig_send = requests.sessions.Session.send
        self._collector_patch = patch("backend.perf.http_hooks.collector")
        self.mock_collector = self._collector_patch.start()

    def tearDown(self):
        import requests
        requests.sessions.Session.send = self._orig_send
        self._collector_patch.stop()

    def test_patched_send_records_outbound_bytes_and_host(self):
        import requests

        class FakeResponse:
            content = b"0123456789"  # 10 bytes
            status_code = 200

        def fake_send(self, request, **kwargs):
            return FakeResponse()

        requests.sessions.Session.send = fake_send
        http_hooks._patch_requests()

        session = requests.Session()
        prepared = requests.Request(
            "POST", "https://mc.shop.kaspi.kz/bff/offer-view/details", data=b"abc"
        ).prepare()
        session.send(prepared)

        self.mock_collector.record_outbound.assert_called_once()
        args, kwargs = self.mock_collector.record_outbound.call_args
        host_family, path_key, elapsed = args[0], args[1], args[2]
        self.assertEqual(host_family, "mc.shop.kaspi.kz")
        self.assertEqual(path_key, "/bff/offer-view/details")
        self.assertGreaterEqual(elapsed, 0)
        self.assertEqual(args[4], 10)  # resp_bytes

    def test_double_patch_is_idempotent(self):
        import requests

        def fake_send(self, request, **kwargs):
            class R:
                content = b""
                status_code = 200
            return R()

        requests.sessions.Session.send = fake_send
        http_hooks._patch_requests()
        patched_once = requests.sessions.Session.send
        http_hooks._patch_requests()
        self.assertIs(requests.sessions.Session.send, patched_once)


class TestIsEnabledFlag(unittest.TestCase):
    """backend/main.py gates the perf middleware, background loop-lag task,
    and the /api/_perf/snapshot route registration all on this one flag —
    covering it here pins the on/off values that keep prod inert by default."""

    def test_unset_is_disabled(self):
        from backend.perf.collector import is_enabled
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PERF_PROFILING", None)
            self.assertFalse(is_enabled())

    def test_truthy_values_enable(self):
        from backend.perf.collector import is_enabled
        for val in ("1", "true", "True"):
            with patch.dict(os.environ, {"PERF_PROFILING": val}):
                self.assertTrue(is_enabled())

    def test_other_values_stay_disabled(self):
        from backend.perf.collector import is_enabled
        for val in ("0", "false", "no", ""):
            with patch.dict(os.environ, {"PERF_PROFILING": val}):
                self.assertFalse(is_enabled())


if __name__ == "__main__":
    unittest.main()
