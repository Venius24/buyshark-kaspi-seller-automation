"""
Окно слепоты: минуты, когда правка в кабинете будет потеряна.

Против этого нет ловушки — только предупреждение. Файл объявляет состояние на
момент снимка, и правку, сделанную человеком за время полёта, он откатит, а
сверка этого не увидит: объявленное и фактическое после отката совпадут.
Поэтому окно обязано быть предъявимо, а не жить в голове у того, кто читал код.
"""
import datetime

import pytest

from backend.dumper import feed_state


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        feed_state, "_state_path",
        lambda store_id: tmp_path / "{}.json".format(store_id),
    )
    return "store-window"


def test_window_defaults_to_the_measured_five_minutes(store):
    """
    Замер на Бархане 2026-08-26: снимок 19:05:53, применено 19:10:31 —
    4 минуты 38 секунд на 1100 офферах.
    """
    assert feed_state.expected_flight_seconds(store) == feed_state.DEFAULT_FLIGHT_SECONDS


def test_window_follows_the_measured_processing_time(store):
    """Время разбора растёт с каталогом — константа врала бы в опасную сторону."""
    feed_state.save(store, last_processing_seconds=900.0)

    assert feed_state.expected_flight_seconds(store) == 900.0


def test_window_counts_from_the_snapshot_not_from_the_send(store):
    """Значения устарели в момент снимка, а не когда файл ушёл."""
    taken = datetime.datetime.now() - datetime.timedelta(seconds=60)
    feed_state.mark_sent(store, "file.xml", taken)

    window = feed_state.blind_window(store)

    expected = taken + datetime.timedelta(seconds=feed_state.DEFAULT_FLIGHT_SECONDS)
    assert abs((window["until"] - expected).total_seconds()) < 1
    # Минута уже прошла — до закрытия осталось меньше полного окна.
    assert window["seconds_left"] < feed_state.DEFAULT_FLIGHT_SECONDS


def test_expired_window_is_not_a_window(store):
    feed_state.mark_sent(
        store, "file.xml", datetime.datetime.now() - datetime.timedelta(hours=1)
    )

    assert feed_state.blind_window(store) is None


def test_settling_closes_the_window(store):
    feed_state.mark_sent(store, "file.xml", datetime.datetime.now())
    assert feed_state.blind_window(store) is not None

    class _Report:
        file_id = "f1"

        def summary(self):
            return "ок"

    feed_state.mark_settled(store, _Report())

    assert feed_state.blind_window(store) is None
    assert feed_state.load(store)["in_flight"] is None


def test_no_window_when_nothing_is_flying(store):
    assert feed_state.blind_window(store) is None


def test_broken_timestamp_does_not_crash_readers(store):
    """Читателей у окна будет несколько; ни один не должен упасть на мусоре."""
    feed_state.save(store, in_flight_until="не-дата")

    assert feed_state.blind_window(store) is None
