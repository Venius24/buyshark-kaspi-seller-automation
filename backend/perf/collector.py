"""
Потокобезопасные счётчики производительности. Один процесс-синглтон
(`collector`), пишут в него и event loop (через middleware), и рабочие
потоки (через http_hooks) — отсюда threading.Lock, а не asyncio-примитивы.

Всё поведение спрятано за PERF_PROFILING=1 (см. is_enabled()), чтобы в
обычном проде это был чистый ноль накладных расходов и ноль новых
эндпоинтов.
"""

import os
import time
import threading
from collections import deque
from typing import Dict


def is_enabled() -> bool:
    return os.getenv("PERF_PROFILING", "").strip() in ("1", "true", "True")


class _KeyStats:
    __slots__ = ("count", "total_seconds", "max_seconds", "durations", "req_bytes", "resp_bytes")

    def __init__(self):
        self.count = 0
        self.total_seconds = 0.0
        self.max_seconds = 0.0
        # Сырые длительности, не агрегированные перцентили — extend/append
        # дешевле, чем пересчитывать percentile на каждый вызов.
        self.durations = deque(maxlen=2000)
        self.req_bytes = 0
        self.resp_bytes = 0

    def record(self, seconds: float, req_bytes: int = 0, resp_bytes: int = 0):
        self.count += 1
        self.total_seconds += seconds
        if seconds > self.max_seconds:
            self.max_seconds = seconds
        self.durations.append(seconds)
        self.req_bytes += req_bytes
        self.resp_bytes += resp_bytes

    def _percentile(self, p: float) -> float:
        if not self.durations:
            return 0.0
        ordered = sorted(self.durations)
        idx = min(len(ordered) - 1, int(len(ordered) * p))
        return ordered[idx]

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "total_seconds": round(self.total_seconds, 4),
            "avg_ms": round((self.total_seconds / self.count) * 1000, 2) if self.count else 0.0,
            "p50_ms": round(self._percentile(0.50) * 1000, 2),
            "p95_ms": round(self._percentile(0.95) * 1000, 2),
            "max_ms": round(self.max_seconds * 1000, 2),
            "req_bytes": self.req_bytes,
            "resp_bytes": self.resp_bytes,
            "total_bytes": self.req_bytes + self.resp_bytes,
        }


class PerfCollector:
    def __init__(self):
        self._lock = threading.Lock()
        self._inbound: Dict[str, _KeyStats] = {}
        self._outbound_by_path: Dict[str, _KeyStats] = {}
        self._outbound_by_host: Dict[str, _KeyStats] = {}
        self._loop_lag_samples = deque(maxlen=20_000)
        self._loop_lag_seconds_over_100ms = 0.0
        self._loop_lag_max_seconds = 0.0
        self._start_time = time.time()

    def record_inbound(self, route_key: str, seconds: float, resp_bytes: int = 0):
        with self._lock:
            self._inbound.setdefault(route_key, _KeyStats()).record(seconds, resp_bytes=resp_bytes)

    def record_outbound(self, host_family: str, path_key: str, seconds: float, req_bytes: int = 0, resp_bytes: int = 0):
        with self._lock:
            self._outbound_by_path.setdefault(f"{host_family} {path_key}", _KeyStats()).record(seconds, req_bytes, resp_bytes)
            self._outbound_by_host.setdefault(host_family, _KeyStats()).record(seconds, req_bytes, resp_bytes)

    def record_loop_lag(self, seconds: float):
        with self._lock:
            self._loop_lag_samples.append(seconds)
            if seconds > self._loop_lag_max_seconds:
                self._loop_lag_max_seconds = seconds
            if seconds > 0.1:
                self._loop_lag_seconds_over_100ms += seconds

    def snapshot(self, label: str = "") -> dict:
        with self._lock:
            uptime = time.time() - self._start_time
            lag_sorted = sorted(self._loop_lag_samples)

            def lag_pct(p: float) -> float:
                if not lag_sorted:
                    return 0.0
                idx = min(len(lag_sorted) - 1, int(len(lag_sorted) * p))
                return lag_sorted[idx]

            return {
                "label": label,
                "uptime_seconds": round(uptime, 1),
                "inbound": {k: v.to_dict() for k, v in self._inbound.items()},
                "outbound_by_path": {k: v.to_dict() for k, v in self._outbound_by_path.items()},
                "outbound_by_host": {k: v.to_dict() for k, v in self._outbound_by_host.items()},
                "event_loop_lag": {
                    "samples": len(lag_sorted),
                    "p50_ms": round(lag_pct(0.50) * 1000, 2),
                    "p95_ms": round(lag_pct(0.95) * 1000, 2),
                    "max_ms": round(self._loop_lag_max_seconds * 1000, 2),
                    "seconds_blocked_over_100ms": round(self._loop_lag_seconds_over_100ms, 2),
                    "blocked_ratio": round(self._loop_lag_seconds_over_100ms / uptime, 6) if uptime else 0.0,
                },
            }


collector = PerfCollector()
