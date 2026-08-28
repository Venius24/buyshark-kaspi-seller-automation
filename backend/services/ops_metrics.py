"""Счётчики операционного здоровья системы (ночной аудит 2026-08-24).

Лёгкий потокобезопасный сборщик без зависимостей: воркеры и обёртки Kaspi
инкрементируют счётчики и дёргают точки интереса, а ops_digest_worker раз в
сутки шлёт владельцу сводку и немедленно кричит при «логин-шторме».

Дизайн: это ЛИСТОВОЙ модуль (импортирует только stdlib) — его можно
импортить откуда угодно, включая state_helpers, не создавая циклов.
"""
import os
import threading
import time
from collections import deque
from datetime import datetime

_lock = threading.Lock()

# Именованные счётчики: kaspi_429, kaspi_429_exhausted, kaspi_http_error_5xx,
# mc_relogin_ok, mc_relogin_failed, mc_relogin_skipped_cooldown,
# webhook_fail. Снапшот для утренней сводки забирает с обнулением.
_counters: dict = {}

# Кольцо последних примечательных событий (не сырые логи, а «что случилось»).
_events: deque = deque(maxlen=300)

# Времена неудачных релогинов per-store — для детектора шторма.
_mc_fail_times: deque = deque(maxlen=500)  # (monotonic_ts, store_id)

_STORM_WINDOW_SECONDS = int(os.getenv("OPS_STORM_WINDOW_SECONDS", "600"))
_STORM_THRESHOLD = int(os.getenv("OPS_STORM_THRESHOLD", "3"))
_STORM_SUPPRESS_SECONDS = int(os.getenv("OPS_STORM_SUPPRESS_SECONDS", "3600"))
_last_storm_alert_at = 0.0


def inc(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def record(text: str) -> None:
    line = f"{datetime.now().strftime('%m-%d %H:%M:%S')} {text}"
    with _lock:
        _events.append(line)


def mc_relogin_ok(store_id) -> None:
    inc("mc_relogin_ok")
    record(f"MC релогин OK ({store_id})")


def mc_relogin_failed(store_id, reason: str = "") -> None:
    inc("mc_relogin_failed")
    record(f"MC релогин FAILED ({store_id}): {reason}")
    with _lock:
        _mc_fail_times.append((time.monotonic(), str(store_id)))


def storm_check() -> dict:
    """Возвращает {'storm': bool, 'fails_in_window': int} и учитывает
    подавление повторных тревог. Вызывает digest-воркер раз в минуту."""
    global _last_storm_alert_at
    now_mono = time.monotonic()
    with _lock:
        recent = [sid for ts, sid in _mc_fail_times if now_mono - ts <= _STORM_WINDOW_SECONDS]
    in_window = len(recent)
    should_alert = (
        in_window >= _STORM_THRESHOLD
        and (now_mono - _last_storm_alert_at) >= _STORM_SUPPRESS_SECONDS
    )
    if should_alert:
        with _lock:
            _last_storm_alert_at = now_mono
        return {"storm": True, "fails_in_window": in_window, "stores": sorted(set(recent))}
    return {"storm": False, "fails_in_window": in_window}


def snapshot(reset: bool = True) -> dict:
    """Счётчики с прошлого снимка (+ события). reset=True обнуляет счётчики —
    утренняя сводка показывает ровно сутки."""
    with _lock:
        snap = dict(_counters)
        events = list(_events)
        if reset:
            for k in _counters:
                _counters[k] = 0
            _events.clear()
    return {"counters": snap, "events": events}
