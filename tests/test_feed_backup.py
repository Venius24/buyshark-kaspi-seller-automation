"""
Точки восстановления: что переживает ротацию, а что нет.

Проверяется не «gzip умеет жать», а два свойства, ошибка в каждом из которых
обнаружится ровно тогда, когда копия понадобится: файл в полёте остаётся
читаемым, и последняя копия не удаляется никогда.
"""
import datetime
import gzip

import pytest

from backend.dumper import feed_backup, feed_state


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        feed_backup, "_backup_dir",
        lambda store_id: _mk(tmp_path / str(store_id) / "backups"),
    )
    monkeypatch.setattr(
        feed_state, "_state_path", lambda store_id: tmp_path / "{}.json".format(store_id)
    )
    return "store-backup"


def _mk(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sent_file(store, name, data=b"<xml/>", age_days=0):
    """Отправленный файл лежит рядом с backups/, как его кладёт uploader."""
    import os
    import time

    path = feed_backup._backup_dir(store).parent / name
    path.write_bytes(data)
    path.with_suffix(path.suffix + ".meta.json").write_bytes(b"{}")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return path


def test_saves_and_reads_back(store):
    path = feed_backup.save(store, b"<kaspi_catalog/>" * 100, "kaspi-export")

    assert path.name.startswith("kaspi-export-")
    assert feed_backup.load(path) == b"<kaspi_catalog/>" * 100


def test_compression_is_the_point(store):
    """Прайс-лист — тысяча однотипных блоков; без выигрыша затея бессмысленна."""
    data = b"<offer sku='A'><model>x</model></offer>" * 2000
    path = feed_backup.save(store, data, "sent")

    assert path.stat().st_size < len(data) / 5


def test_same_generation_is_not_duplicated(store):
    """
    Выгрузку Kaspi мы качаем чаще, чем она перегенерируется. Копия у одной
    генерации должна быть одна.
    """
    at = datetime.datetime(2026, 8, 28, 18, 32)
    feed_backup.save(store, b"<a/>", "kaspi-export", at)
    feed_backup.save(store, b"<a/>", "kaspi-export", at)

    assert len(feed_backup.restore_points(store, "kaspi-export")) == 1


def test_file_in_flight_is_never_touched(store):
    """По нему пойдёт сверка после вердикта — ей нужен и XML, и meta."""
    path = _sent_file(store, "buyshark-1.xml", age_days=10)
    feed_state.mark_sent(store, "buyshark-1.xml", datetime.datetime.now())

    feed_backup.rotate(store)

    assert path.exists()
    assert path.with_suffix(path.suffix + ".meta.json").exists()


def test_settled_file_is_compressed_away(store):
    path = _sent_file(store, "buyshark-2.xml", data=b"<x/>" * 500)

    feed_backup.rotate(store)

    assert not path.exists()
    assert not path.with_suffix(path.suffix + ".meta.json").exists()
    kinds = {p["kind"] for p in feed_backup.restore_points(store)}
    assert kinds == {"sent", "sent-meta"}


def test_old_copies_are_removed(store):
    now = datetime.datetime.now()
    feed_backup.save(store, b"<new/>", "kaspi-export", now)
    feed_backup.save(store, b"<mid/>", "kaspi-export", now - datetime.timedelta(days=1))
    feed_backup.save(store, b"<old/>", "kaspi-export", now - datetime.timedelta(days=9))

    feed_backup.rotate(store)

    left = [p["at"].date() for p in feed_backup.restore_points(store, "kaspi-export")]
    assert len(left) == 2
    assert (now - datetime.timedelta(days=9)).date() not in left


def test_last_copy_survives_any_age(store):
    """
    Фид может простоять выключенным неделю, пока чинят артикул. Остаться без
    точки восстановления именно в этот момент — худший из исходов.
    """
    ancient = datetime.datetime.now() - datetime.timedelta(days=90)
    feed_backup.save(store, b"<only/>", "kaspi-export", ancient)

    feed_backup.rotate(store)

    points = feed_backup.restore_points(store, "kaspi-export")
    assert len(points) == 1
    assert feed_backup.load(points[0]["path"]) == b"<only/>"


def test_kinds_are_rotated_independently(store):
    """
    У выгрузки и отправленных файлов разная частота. Общий счётчик выбил бы
    редкий вид ради частого.
    """
    old = datetime.datetime.now() - datetime.timedelta(days=30)
    feed_backup.save(store, b"<export/>", "kaspi-export", old)
    for i in range(5):
        feed_backup.save(store, b"<sent/>", "sent", old + datetime.timedelta(seconds=i))

    feed_backup.rotate(store)

    assert len(feed_backup.restore_points(store, "kaspi-export")) == 1
    assert len(feed_backup.restore_points(store, "sent")) == 1


def test_rotation_survives_a_broken_name(store):
    """Чужой файл в каталоге не должен ронять вычистку."""
    (feed_backup._backup_dir(store) / "мусор.xml.gz").write_bytes(gzip.compress(b"?"))

    assert feed_backup.rotate(store)["removed"] == 0
