"""
Фоновая задача, измеряющая реальный лаг event loop — сколько раз и на
сколько дольше запланированного луп "проспал" из-за того, что был занят
синхронным блокирующим вызовом где-то ещё в приложении. Это главная метрика
проекта: переводит "сервисом невозможно пользоваться" в измеримое число
секунд блокировки в час.
"""

import asyncio
import time

from backend.perf.collector import collector

_INTERVAL_SECONDS = 0.25


async def monitor_event_loop_lag(interval_seconds: float = _INTERVAL_SECONDS):
    next_wake = time.perf_counter() + interval_seconds
    while True:
        await asyncio.sleep(interval_seconds)
        now = time.perf_counter()
        overshoot = now - next_wake
        if overshoot > 0:
            collector.record_loop_lag(overshoot)
        next_wake = now + interval_seconds
