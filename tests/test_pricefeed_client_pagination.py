import os
import sys
import unittest

sys.path.append(os.getcwd())

from backend.services import pricefeed_client
from backend.services.pricefeed_client import _fetch_all_pages, PriceFeedAuthError


def _page(content, total_pages):
    return {"content": content, "totalPages": total_pages}


class TestFetchAllPages(unittest.TestCase):
    def test_respects_total_pages(self):
        pages = {
            0: _page([{"sku": "a"}], 3),
            1: _page([{"sku": "b"}], 3),
            2: _page([{"sku": "c"}], 3),
        }
        items, pages_fetched, truncated = _fetch_all_pages(lambda n: pages[n], max_pages=10, delay_s=0)
        self.assertEqual([i["sku"] for i in items], ["a", "b", "c"])
        self.assertEqual(pages_fetched, 3)
        self.assertFalse(truncated)

    def test_stops_at_max_pages_and_marks_truncated(self):
        pages = {n: _page([{"sku": str(n)}], 10) for n in range(10)}
        items, pages_fetched, truncated = _fetch_all_pages(lambda n: pages[n], max_pages=3, delay_s=0)
        self.assertEqual(len(items), 3)
        self.assertEqual(pages_fetched, 3)
        self.assertTrue(truncated)

    def test_single_page(self):
        pages = {0: _page([{"sku": "only"}], 1)}
        items, pages_fetched, truncated = _fetch_all_pages(lambda n: pages[n], max_pages=10, delay_s=0)
        self.assertEqual(len(items), 1)
        self.assertEqual(pages_fetched, 1)
        self.assertFalse(truncated)

    def test_empty_result(self):
        pages = {0: _page([], 0)}
        items, pages_fetched, truncated = _fetch_all_pages(lambda n: pages[n], max_pages=10, delay_s=0)
        self.assertEqual(items, [])
        self.assertEqual(pages_fetched, 0)
        self.assertFalse(truncated)

    def test_error_mid_pagination_propagates(self):
        def fetch_page(n):
            if n == 0:
                return _page([{"sku": "a"}], 5)
            raise PriceFeedAuthError("сессия отвалилась")

        with self.assertRaises(PriceFeedAuthError):
            _fetch_all_pages(fetch_page, max_pages=10, delay_s=0)


class TestExtractMerchants(unittest.TestCase):
    def test_handles_plain_list(self):
        merchants = pricefeed_client._extract_merchants([{"id": "1", "name": "Shop A"}])
        self.assertEqual(len(merchants), 1)
        self.assertEqual(merchants[0].id, "1")

    def test_handles_paginated_wrapper(self):
        merchants = pricefeed_client._extract_merchants({"content": [{"id": "2", "name": "Shop B"}]})
        self.assertEqual(len(merchants), 1)
        self.assertEqual(merchants[0].name, "Shop B")

    def test_empty(self):
        self.assertEqual(pricefeed_client._extract_merchants({"content": []}), [])
        self.assertEqual(pricefeed_client._extract_merchants([]), [])


if __name__ == "__main__":
    unittest.main()
