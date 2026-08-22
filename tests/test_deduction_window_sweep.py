import asyncio
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from backend.services import stock_sync_worker as worker


class WindowSweepTestCase(unittest.TestCase):
    """
    Двухуровневая выборка авто-списания: узкое окно на каждом 30с-тике,
    полный 13-дневный свип при старте процесса и раз в час.

    Гарантия, ради которой затеяно (решение владельца): сужение окна не
    должно привести к потере списаний. Поэтому свип обязателен при старте,
    периодичен дальше и НЕ сдвигает отметку при неудачной выборке.
    """

    STORE = "store-w1"
    TOKEN = "tok"

    def setUp(self):
        worker._last_full_sweep_at.clear()

    def _run_cycle(self, fetch_side_effect):
        """Один цикл списания с подменённой выборкой; возвращает (результат, список окон)."""
        windows = []

        def fake_tabs(token, days=13):
            windows.append(days)
            if isinstance(fetch_side_effect, Exception):
                raise fetch_side_effect
            return {"cargo_assembly": [], "wait_for_courier": [], "transmitted": []}

        with patch.object(worker.KaspiAPI, "get_all_packaging_tabs", side_effect=fake_tabs):
            result = asyncio.run(
                worker.process_packaging_auto_deduction(self.TOKEN, self.STORE)
            )
        return result, windows

    def test_first_cycle_after_startup_is_full_sweep(self):
        """Старт процесса = полный 13-дневный обход: ловит всё накопленное,
        пока сервис лежал. Это база гарантии непотери."""
        _, windows = self._run_cycle(None)
        self.assertEqual(windows, [worker.FULL_SWEEP_WINDOW_DAYS])

    def test_subsequent_cycles_use_hot_window(self):
        self._run_cycle(None)
        _, windows = self._run_cycle(None)
        self.assertEqual(windows, [worker.DEDUCTION_HOT_WINDOW_DAYS])

    def test_sweep_repeats_after_interval_elapsed(self):
        self._run_cycle(None)
        worker._last_full_sweep_at[self.STORE] = (
            time.monotonic() - worker.DEDUCTION_FULL_SWEEP_INTERVAL_SECONDS - 1
        )
        _, windows = self._run_cycle(None)
        self.assertEqual(windows, [worker.FULL_SWEEP_WINDOW_DAYS])
        # И следующий тик снова горячий.
        _, windows = self._run_cycle(None)
        self.assertEqual(windows, [worker.DEDUCTION_HOT_WINDOW_DAYS])

    def test_failed_fetch_does_not_advance_sweep_mark(self):
        """Упавшая выборка не сдвигает отметку свипа — иначе провал полного
        обхода превратил бы следующие полчаса в слепые горячие тики."""
        result, _ = self._run_cycle(RuntimeError("kaspi down"))
        self.assertFalse(result["success"])
        self.assertNotIn(self.STORE, worker._last_full_sweep_at)

        # Следующий тик обязан остаться полным свипом.
        _, windows = self._run_cycle(None)
        self.assertEqual(windows, [worker.FULL_SWEEP_WINDOW_DAYS])

    def test_sweep_marks_are_per_store(self):
        other = "store-w2"
        with patch.object(worker.KaspiAPI, "get_all_packaging_tabs",
                          side_effect=lambda token, days=13: {"cargo_assembly": [], "wait_for_courier": [], "transmitted": []}):
            asyncio.run(worker.process_packaging_auto_deduction(self.TOKEN, self.STORE))
            # Другой стор ещё ни разу не циклился — его первый проход обязан быть свипом.
            days_seen = []
            orig = worker.KaspiAPI.get_all_packaging_tabs
            with patch.object(worker.KaspiAPI, "get_all_packaging_tabs",
                              side_effect=lambda token, days=13: (days_seen.append(days), {"cargo_assembly": [], "wait_for_courier": [], "transmitted": []})[1]):
                asyncio.run(worker.process_packaging_auto_deduction(self.TOKEN, other))
        self.assertEqual(days_seen, [worker.FULL_SWEEP_WINDOW_DAYS])


if __name__ == "__main__":
    unittest.main()
