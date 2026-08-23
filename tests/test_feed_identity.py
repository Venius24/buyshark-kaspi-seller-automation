"""
Тесты источника полей идентичности.

Смысл модуля в одном: `<model>` и `<brand>` берутся из файла, который Kaspi
сама приняла, а не из BFF. Причина — бренд в фиде это слаг (`volat`), а в BFF
display-имя («Волат»), и вывести одно из другого невозможно. Отправка файла с
display-именами переписала бы бренды у 770 карточек Бархана (замерено 24.08).
"""
import pytest

from backend.dumper import feed_identity
from backend.dumper.feed_identity import (
    apply_identity,
    fetch_accepted_identity,
    parse_identity,
)

ACCEPTED = b"""<?xml version="1.0" encoding="UTF-8"?><kaspi_catalog xmlns="kaspiShopping">
    <offers>
        <offer sku="D979-0036-2">
            <model>Ledgehammer</model>
            <brand>volat</brand>
        </offer>
        <offer sku="S517-0015">
            <model/>
            <brand/>
        </offer>
        <offer sku="H477-0009\t">
            <model>H477-0009\t</model>
            <brand>barkhan</brand>
        </offer>
        <offer sku="K733-0110">
            <model>Fiskars &amp; Co</model>
            <brand>fiskars</brand>
        </offer>
    </offers>
</kaspi_catalog>
"""


def test_parses_models_and_brands():
    ident = parse_identity(ACCEPTED)
    assert ident["D979-0036-2"] == {"name": "Ledgehammer", "brand": "volat"}


def test_empty_tags_become_none():
    """Пустой тег — это «значения нет», а не пустая строка, которую мы допишем."""
    assert parse_identity(ACCEPTED)["S517-0015"] == {"name": None, "brand": None}


def test_key_is_normalised_like_an_xml_attribute():
    """У трёх артикулов Бархана в SKU живёт табуляция; без нормализации не совпадёт."""
    ident = parse_identity(ACCEPTED)
    assert "H477-0009 " in ident
    assert "H477-0009\t" not in ident


def test_unescapes_entities():
    assert parse_identity(ACCEPTED)["K733-0110"]["name"] == "Fiskars & Co"


def test_apply_overrides_bff_values():
    """Главное свойство: display-имя из BFF заменяется слагом из принятого файла."""
    offers = [{"merchantSku": "D979-0036-2", "name": "Молоток", "brand": "Волат"}]
    result, stats = apply_identity(offers, parse_identity(ACCEPTED))
    assert result[0]["brand"] == "volat"
    assert result[0]["name"] == "Ledgehammer"
    assert stats == {"applied": 1, "unknown": []}


def test_offer_outside_accepted_file_gets_empty_fields():
    """Выдумывать нельзя: именно так в каталог уехали 65 заглушек «Товар <sku>»."""
    offers = [{"merchantSku": "НОВЫЙ-001", "name": "Товар НОВЫЙ-001", "brand": "Без бренда"}]
    result, stats = apply_identity(offers, parse_identity(ACCEPTED))
    assert result[0]["name"] is None and result[0]["brand"] is None
    assert stats["unknown"] == ["НОВЫЙ-001"]


def test_apply_does_not_mutate_input():
    offers = [{"merchantSku": "D979-0036-2", "brand": "Волат"}]
    apply_identity(offers, parse_identity(ACCEPTED))
    assert offers[0]["brand"] == "Волат"


def test_tab_sku_matches_bff_value():
    offers = [{"merchantSku": "H477-0009\t", "brand": "Barkhan"}]
    result, stats = apply_identity(offers, parse_identity(ACCEPTED))
    assert result[0]["brand"] == "barkhan"
    assert stats["unknown"] == []


# ── Загрузка принятого файла ────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


def _wire(monkeypatch, card, content=ACCEPTED, download_status=200):
    def fake(method, url, store_id=None, headers=None, params=None, timeout=None, **kw):
        if url == feed_identity.LAST_FILE_URL:
            return _Resp(200, card)
        return _Resp(download_status, content=content)

    monkeypatch.setattr("backend.state_helpers._mc_api_request", fake)


def test_fetches_and_parses(monkeypatch):
    _wire(monkeypatch, {"id": "f-1", "status": "FLUSHED"})
    file_id, ident = fetch_accepted_identity("store-1", "20186986")
    assert file_id == "f-1"
    assert ident["D979-0036-2"]["brand"] == "volat"


def test_unflushed_file_is_not_an_authority(monkeypatch):
    """Недообработанный файл не является утверждённым состоянием каталога."""
    _wire(monkeypatch, {"id": "f-1", "status": "PROCESSING"})
    assert fetch_accepted_identity("store-1", "20186986") == (None, {})


def test_no_file_at_all(monkeypatch):
    _wire(monkeypatch, {})
    assert fetch_accepted_identity("store-1", "20186986") == (None, {})


def test_download_failure_is_not_fatal(monkeypatch):
    """Отсутствие источника — не падение, а запрет на отправку (решает uploader)."""
    _wire(monkeypatch, {"id": "f-1", "status": "FLUSHED"}, download_status=500)
    assert fetch_accepted_identity("store-1", "20186986") == (None, {})
