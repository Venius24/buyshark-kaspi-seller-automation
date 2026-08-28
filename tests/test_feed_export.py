"""
Выгрузка каталога Kaspi: источник идентичности и точка отката.

Проверяется поведение на границах, а не то, что requests умеет ходить в сеть:
что мы НЕ дёргаем генерацию без спроса, что протухшая выгрузка лучше пустой, и
что по ней нельзя судить об артикулах.
"""
import datetime
import types

import pytest

from backend.dumper import feed_export


EXPORT = """<?xml version="1.0" encoding="UTF-8"?>
<kaspi_catalog xmlns="kaspiShopping" date="2026-08-28 18:32">
    <company>20186986</company>
    <merchantid>20186986</merchantid>
    <offers>
        <offer sku="D472-0028">
            <model>Bepeвка Barkhan D472-0028</model>
            <brand>barkhan</brand>
        </offer>
        <offer sku="H974-0006">
            <model/>
            <brand>barkhan</brand>
        </offer>
    </offers>
</kaspi_catalog>""".encode("utf-8")


class _Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        feed_export, "_cache_path",
        lambda store_id, available: tmp_path / "{}-{}.xml".format(store_id, available),
    )
    return "store-export"


def _wire(monkeypatch, calls, *, status_payload=None, file_resp=None):
    """Подменить транспорт MC и записывать, что именно дёрнули."""
    def fake(method, url, **kw):
        calls.append(url)
        if "export/trigger" in url:
            return _Resp(200, {})
        if "export/status" in url:
            return _Resp(200, status_payload or {"fileName": "20186986/ACTIVE.xml", "status": "FINISHED"})
        return file_resp if file_resp is not None else _Resp(200, content=EXPORT)

    monkeypatch.setattr("backend.state_helpers._mc_api_request", fake)


def test_reads_generation_date_from_the_file(store):
    assert feed_export.parse_generated_at(EXPORT) == datetime.datetime(2026, 8, 28, 18, 32)


def test_does_not_trigger_generation_by_default(store, monkeypatch):
    """
    Скачивание — бесплатно, генерация — нет (429). Читатель не имеет права
    дёргать дорогое, не спросив.
    """
    calls = []
    _wire(monkeypatch, calls)

    feed_export.fetch_export(store, "20186986")

    assert not any("trigger" in u for u in calls)
    assert any("export/status" in u for u in calls)


def test_trigger_only_on_demand(store, monkeypatch):
    calls = []
    _wire(monkeypatch, calls)

    feed_export.fetch_export(store, "20186986", refresh=True)

    assert any("trigger" in u for u in calls)


def test_unfinished_export_is_refused(store, monkeypatch):
    calls = []
    _wire(monkeypatch, calls, status_payload={"status": "PROCESSING"})

    with pytest.raises(feed_export.ExportUnavailable):
        feed_export.fetch_export(store, "20186986")


def test_identity_covers_offers_and_reports_empty_model(store, monkeypatch):
    calls = []
    _wire(monkeypatch, calls)

    source, identity = feed_export.identity_from_export(store, "20186986")

    assert "выгрузка Kaspi" in source
    assert identity["D472-0028"]["brand"] == "barkhan"
    # Пустой <model/> — не дырка в данных, а правда каталога: у Kaspi таких 115.
    assert identity["H974-0006"]["name"] is None


def test_stale_export_beats_no_export(store, monkeypatch, tmp_path, caplog):
    """
    Названия меняются несравнимо реже цен. Протухшая выгрузка даёт старое имя,
    отсутствие выгрузки — пустое поле навсегда.
    """
    feed_export._cache_path(store, "ACTIVE").write_bytes(EXPORT)

    def dead(*_a, **_kw):
        raise RuntimeError("кабинет недоступен")

    monkeypatch.setattr("backend.state_helpers._mc_api_request", dead)

    source, identity = feed_export.identity_from_export(store, "20186986", max_age_hours=0)

    assert identity["D472-0028"]["brand"] == "barkhan"
    assert source is not None


def test_no_export_at_all_returns_nothing(store, monkeypatch):
    """Пусто — значит пусто. Молча выдумать идентичность нельзя."""
    def dead(*_a, **_kw):
        raise RuntimeError("кабинет недоступен")

    monkeypatch.setattr("backend.state_helpers._mc_api_request", dead)

    assert feed_export.identity_from_export(store, "20186986") == (None, {})


def test_skus_are_not_an_article_reference():
    """
    Замер 25.08: Kaspi, выгружая собственный каталог, теряет табуляцию в `sku`
    так же, как теряем её мы. Судить по выгрузке о битых артикулах нельзя —
    это делает feed_guard по BFF.
    """
    broken = EXPORT.replace(b'sku="D472-0028"', b'sku="D472-0028 "')
    assert "D472-0028 " in feed_export.skus(broken)
    assert "D472-0028" not in feed_export.skus(broken)
