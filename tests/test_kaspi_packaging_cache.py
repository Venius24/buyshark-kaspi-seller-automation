import os
import sys
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


if __name__ == "__main__":
    unittest.main()
