import os
import sys
import time
import unittest
from itertools import islice
from unittest.mock import patch, MagicMock

sys.path.append(os.getcwd())

from backend.kaspi_wrapper import KaspiAPI


class TestFetchPagesBatched(unittest.TestCase):
    """
    Категория 1 (B.1): KaspiAPI._fetch_pages_batched() is the shared bounded-
    concurrency pagination primitive behind get_orders_with_products,
    get_transfers, and get_all_packaging_tabs. Correctness here matters more
    than in any single caller, since a bug would silently corrupt order data
    across all three.
    """

    def test_yields_in_ascending_page_order_even_if_fetched_out_of_order(self):
        # Page 0 is the slowest to complete — if the generator yielded in
        # completion order instead of page order, page 2 would come first.
        # _fetch_pages_batched() is an unbounded generator (keeps fetching
        # further batches forever, exactly like the old `while True` loops
        # it replaced) — islice() takes only the first batch without
        # exhausting/hanging on the rest, mirroring how a real caller stops
        # after detecting its own termination condition.
        def fetch_page(page_number):
            time.sleep(0.002 * (3 - page_number))  # tiny, just enough to reorder completion
            return f"page-{page_number}"

        seen = [p for p, _ in islice(KaspiAPI._fetch_pages_batched(fetch_page, max_workers=3), 3)]
        self.assertEqual(seen, [0, 1, 2])

    def test_caller_can_stop_after_first_batch(self):
        calls = []

        def fetch_page(page_number):
            calls.append(page_number)
            return {"data": [1] * (100 if page_number == 0 else 0)}

        for page_number, resp in KaspiAPI._fetch_pages_batched(fetch_page, max_workers=3):
            if not resp["data"]:
                break

        # Page 0 alone, then ONE batch (1,2,3) because page 0 had data.
        # Crucially: no SECOND batch (4,5,6) was ever started.
        self.assertEqual(sorted(calls), [0, 1, 2, 3])

    def test_empty_first_page_costs_a_single_request(self):
        """
        Раньше батч из max_workers страниц уходил всегда, ещё до чтения
        первого ответа: пустая выборка стоила 3 запроса вместо одного. Под
        радаром заказов (тик 20с) и авто-списанием (30с) это давало сотни
        лишних обращений к Kaspi в час — см. tests/test_kaspi_paging.py.
        """
        calls = []

        def fetch_page(page_number):
            calls.append(page_number)
            return {"data": []}

        for _page_number, resp in KaspiAPI._fetch_pages_batched(fetch_page, max_workers=3):
            if not resp["data"]:
                break

        self.assertEqual(calls, [0])

    def test_exception_in_any_page_propagates_to_caller(self):
        def fetch_page(page_number):
            if page_number == 1:
                raise ValueError("simulated network failure")
            return {"data": [1]}

        with self.assertRaises(ValueError):
            for _page_number, _resp in KaspiAPI._fetch_pages_batched(fetch_page, max_workers=3):
                pass

    def test_pages_within_a_batch_run_concurrently(self):
        SLEEP = 0.15

        def fetch_page(page_number):
            time.sleep(SLEEP)
            return page_number

        start = time.perf_counter()
        # 4 страницы = последовательная нулевая + батч (1,2,3) целиком.
        batch = list(islice(KaspiAPI._fetch_pages_batched(fetch_page, max_workers=3), 4))
        elapsed = time.perf_counter() - start

        self.assertEqual([p for p, _ in batch], [0, 1, 2, 3])
        # Последовательно это заняло бы ~4*SLEEP; у нас нулевая страница плюс
        # один параллельный батч, то есть ~2*SLEEP.
        self.assertLess(elapsed, SLEEP * 3)

    # Ширину батча меряем на выборке, где нулевая страница ПОЛНАЯ: пустая
    # обрывает обход на первом же запросе и про max_workers ничего не скажет.
    @staticmethod
    def _fetcher_with_full_first_page(calls):
        def fetch_page(page_number):
            calls.append(page_number)
            return {"data": [1] * (100 if page_number == 0 else 0)}

        return fetch_page

    def test_default_max_workers_falls_back_to_3_without_env_var(self):
        calls = []
        fetch_page = self._fetcher_with_full_first_page(calls)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KASPI_PAGE_FETCH_MAX_WORKERS", None)
            for _page_number, resp in KaspiAPI._fetch_pages_batched(fetch_page):
                if not resp["data"]:
                    break

        self.assertEqual(sorted(calls), [0, 1, 2, 3])

    def test_max_workers_configurable_via_env_var(self):
        calls = []
        fetch_page = self._fetcher_with_full_first_page(calls)

        with patch.dict(os.environ, {"KASPI_PAGE_FETCH_MAX_WORKERS": "5"}):
            for _page_number, resp in KaspiAPI._fetch_pages_batched(fetch_page):
                if not resp["data"]:
                    break

        self.assertEqual(sorted(calls), [0, 1, 2, 3, 4, 5])


class TestPackagingTabsRequestCost(unittest.TestCase):
    """
    Тот же счёт запросов, но через реального потребителя из горячего пути:
    get_all_packaging_tabs крутится в 30-секундном цикле авто-списания.
    """

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_no_orders_means_single_http_request(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": [], "included": []}
        mock_get.return_value = resp

        result = KaspiAPI.get_all_packaging_tabs("token")

        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(result["counts"]["cargo_assembly"], 0)


if __name__ == "__main__":
    unittest.main()
