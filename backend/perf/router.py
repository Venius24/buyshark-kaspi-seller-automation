"""
GET /api/_perf/snapshot — readout эндпоинт. Регистрируется в backend/main.py
только если PERF_PROFILING=1 (см. collector.is_enabled()); в обычном проде
этого роутера не существует.
"""

import os
import time

from fastapi import APIRouter, Depends

from backend.perf.collector import collector
from backend.routers.auth_v1 import get_current_user

router = APIRouter(prefix="/api/_perf", tags=["perf"])

try:
    import psutil
    _process = psutil.Process(os.getpid())
except ImportError:
    psutil = None
    _process = None


def _process_stats() -> dict:
    if not psutil or not _process:
        return {"available": False}
    with _process.oneshot():
        cpu_percent = _process.cpu_percent(interval=None)
        mem = _process.memory_info()
        num_threads = _process.num_threads()
    return {
        "available": True,
        "cpu_percent": cpu_percent,
        "rss_mb": round(mem.rss / (1024 * 1024), 1),
        "num_threads": num_threads,
    }


def _system_stats() -> dict:
    if not psutil:
        return {"available": False}
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        # os.getloadavg() не существует на Windows (используется в dev на
        # F:\Dev\sbx) — на VPS (Linux) отработает нормально.
        load1 = load5 = load15 = None
    vm = psutil.virtual_memory()
    return {
        "available": True,
        "cpu_count": psutil.cpu_count(),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "load_avg": [load1, load5, load15],
        "mem_total_mb": round(vm.total / (1024 * 1024), 1),
        "mem_used_percent": vm.percent,
        "swap_used_percent": psutil.swap_memory().percent,
    }


@router.get("/snapshot")
async def get_perf_snapshot(label: str = "", user=Depends(get_current_user)):
    data = collector.snapshot(label=label or os.getenv("PERF_LABEL", ""))
    data["process"] = _process_stats()
    data["system"] = _system_stats()
    data["captured_at"] = time.time()
    return data
