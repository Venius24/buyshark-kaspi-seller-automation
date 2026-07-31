import os
import sys
import time
import unittest
from itertools import islice

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

        # One full batch of 3 fetched (0,1,2) even though only page 0 had
        # data — this is the documented "1-2 wasted fast requests" tradeoff.
        # Crucially: no SECOND batch (pages 3,4,5) was ever started.
        self.assertEqual(sorted(calls), [0, 1, 2])

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
        batch = list(islice(KaspiAPI._fetch_pages_batched(fetch_page, max_workers=3), 3))
        elapsed = time.perf_counter() - start

        self.assertEqual([p for p, _ in batch], [0, 1, 2])
        # Sequential would take ~3*SLEEP; concurrent should be close to 1*SLEEP.
        self.assertLess(elapsed, SLEEP * 2)


if __name__ == "__main__":
    unittest.main()
