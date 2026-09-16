"""Радар не должен занимать собой общий пул потоков приложения.

Замер на проде 2026-09-16: PUT /api/stock-groups = 54.07с. Запрос пришёл через
5с после «📡 [Radar Worker] Запуск сканирования для 316 товаров» и ответил за
секунду до конца цикла; вне скана та же ручка отвечает за 0.35с, GIL при этом
занят на 7.5% — никто не считал, все ждали слот.

Причина: asyncio.to_thread кладёт работу в ЕДИНСТВЕННЫЙ дефолтный executor
лупа (min(32, cpu_count+4) = 6 потоков на 2 ядра), а радар заливал туда 316
сетевых задач одним asyncio.gather.
"""

import asyncio
import os
import sys
import unittest

sys.path.append(os.getcwd())


class RadarUsesItsOwnPool(unittest.TestCase):
    def test_crawl_does_not_go_through_the_shared_default_executor(self):
        """asyncio.to_thread здесь — это возврат к 54-секундному PUT."""
        from backend.dumper import radar

        loop = asyncio.new_event_loop()
        try:
            seen = {}

            def probe(master_sku, city_code):
                import threading
                seen["thread"] = threading.current_thread().name
                return [{"price": 1}]

            orig = radar._fetch_offers_sync
            radar._fetch_offers_sync = probe
            try:
                loop.run_until_complete(radar.fetch_competitor_offers("SKU", "750000000"))
            finally:
                radar._fetch_offers_sync = orig
        finally:
            loop.close()

        self.assertTrue(
            seen["thread"].startswith("radar-crawl"),
            f"скан ушёл в общий пул (поток {seen['thread']!r}) — см. docstring модуля",
        )

    def test_radar_pool_stays_as_narrow_as_it_was(self):
        """Пул радара — про изоляцию, не про скорость.

        Исходящий IP этого сервера уже ловил бан Kaspi (см. комментарий про
        KASPI_RADAR_PROXY в radar.py). Шесть — ровно столько, сколько радар
        получал де-факто, деля дефолтный executor; расширять залп к kaspi.kz
        отдельным решением, а не побочным эффектом правки производительности.
        """
        from backend.dumper import radar

        self.assertEqual(radar.RADAR_CRAWL_WORKERS, 6)
        self.assertEqual(radar._crawl_executor._max_workers, 6)


class AppPoolMatchesDbPool(unittest.TestCase):
    def test_sync_db_pool_is_not_narrower_than_the_thread_pool(self):
        """Иначе очередь просто переезжает с потоков на checkout соединения."""
        from backend.main import APP_THREAD_WORKERS
        import inspect
        from backend import state_helpers

        src = inspect.getsource(state_helpers._get_sync_db_session)
        import re
        pool = int(re.search(r"pool_size=(\d+)", src).group(1))
        overflow = int(re.search(r"max_overflow=(\d+)", src).group(1))
        self.assertGreaterEqual(
            pool + overflow, APP_THREAD_WORKERS,
            "потоков больше, чем соединений в пуле — очередь переедет на БД",
        )


if __name__ == "__main__":
    unittest.main()
