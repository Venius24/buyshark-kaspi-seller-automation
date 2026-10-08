"""
Окна sales_sync_worker: Kaspi фильтрует ARCHIVE/COMPLETED по completionDate,
поэтому окна должны без дыр покрывать [since, now] и кончаться ровно в now —
иначе заказы, закрытые после конца окна, теряются.
"""
import datetime
import os
import sys

sys.path.append(os.getcwd())

from backend.services.sales_sync_worker import _windows_since, _WINDOW_DAYS


def _ms(d):
    return int(d.timestamp() * 1000)


def test_windows_cover_since_to_now_without_gaps():
    now = datetime.datetime(2026, 10, 8, 18, 0)
    since = now - datetime.timedelta(days=90)
    windows = _windows_since(since, now)

    assert windows[0][0] == _ms(since)
    assert windows[-1][1] == _ms(now)
    for (_, prev_le), (next_ge, _) in zip(windows, windows[1:]):
        assert next_ge == prev_le
    assert all(le - ge <= _WINDOW_DAYS * 86400 * 1000 for ge, le in windows)


def test_incremental_tick_is_single_window_ending_now():
    now = datetime.datetime(2026, 10, 8, 18, 0)
    windows = _windows_since(now - datetime.timedelta(days=1, hours=8), now)
    assert len(windows) == 1
    assert windows[0][1] == _ms(now)
