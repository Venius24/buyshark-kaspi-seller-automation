"""
Тесты сериализатора прайс-листа Kaspi.

Главный тест здесь — `test_reproduces_reference_feed_byte_for_byte`: фикстура
`tests/fixtures/kaspi_reference_feed.xml` это выдержка из НАСТОЯЩЕГО файла,
который Kaspi приняла у Бархана 15.08.2026 (скачан через
`GET /pricefeed/upload/merchant/files/{id}/download`, в карточке файла
`processedOffersCount: 1153, rejectedOffersCount: 0`). Мы разбираем эталон
обратно в наши продуктовые словари, прогоняем через генератор и сверяем байты.

Так проверяется не «мы согласны сами с собой», а «мы попадаем в схему, которую
Kaspi уже подтвердила приёмкой». Цена ошибки в этом файле — снятый с продажи
каталог (HANDOFF.md §3.67), поэтому сверка именно побайтовая.
"""
import datetime
import pathlib
import re

import pytest

from backend.dumper.xml_feed import (
    FeedSerializationError,
    generate_xml_feed,
)

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES_DIR / "kaspi_reference_feed.xml"
# Второй эталон — выдержка из выгрузки ACTIVE от 2026-08-23 (1110 офферов).
# Взят отдельно от первого, потому что вскрыл расхождение, которого в первом не
# было: Kaspi сохраняет пробелы и табуляции в <model> дословно, а мы их обрезали.
FIXTURE_ACTIVE = FIXTURES_DIR / "kaspi_reference_feed_active.xml"

OFFER_RE = re.compile(r'        <offer sku="(?P<sku>[^"]+)">\n(?P<body>.*?)\n        </offer>', re.S)


def _unescape(text: str) -> str:
    return (
        text.replace("&quot;", '"')
        .replace("&gt;", ">")
        .replace("&lt;", "<")
        .replace("&amp;", "&")
    )


def _parse_reference(xml_text: str):
    """Эталон -> (merchant_id, дата, список продуктовых словарей)."""
    merchant_id = re.search(r"<merchantid>(\d+)</merchantid>", xml_text).group(1)
    stamp = re.search(r'date="([^"]+)"', xml_text).group(1)
    generated_at = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M")

    products = []
    for m in OFFER_RE.finditer(xml_text):
        body = m.group("body")
        model = re.search(r"<model>(.*?)</model>", body, re.S)
        brand = re.search(r"<brand>(.*?)</brand>", body, re.S)
        avail = re.search(
            r'<availability available="([^"]+)" storeId="([^"]+)" preOrder="\d+" stockCount="([\d.]+)"/>',
            body,
        )
        price = re.search(r'<cityprice cityId="\d+">(\d+)</cityprice>', body)
        products.append(
            {
                "merchantSku": _unescape(m.group("sku")),
                "name": _unescape(model.group(1)) if model else None,
                "brand": _unescape(brand.group(1)) if brand else None,
                "available": avail.group(1) == "yes",
                "point_store_id": avail.group(2),
                "stock": float(avail.group(3)),
                "price_num": int(price.group(1)),
            }
        )
    return merchant_id, generated_at, products


@pytest.mark.parametrize("fixture", [FIXTURE, FIXTURE_ACTIVE], ids=["upload", "active"])
def test_reproduces_reference_feed_byte_for_byte(fixture):
    reference = fixture.read_text(encoding="utf-8")
    merchant_id, generated_at, products = _parse_reference(reference)

    assert len(products) == 40, "фикстура повреждена — офферы не разобрались"

    produced = generate_xml_feed(merchant_id, products, generated_at=generated_at).decode("utf-8")

    assert produced == reference


@pytest.mark.parametrize("fixture", [FIXTURE, FIXTURE_ACTIVE], ids=["upload", "active"])
def test_reference_covers_the_awkward_cases(fixture):
    """Фикстура должна содержать именно те случаи, на которых легко ошибиться."""
    reference = fixture.read_text(encoding="utf-8")
    assert "<model/>" in reference, "нет оффера с пустым названием"
    assert "<brand/>" in reference, "нет оффера с пустым брендом"
    assert "&amp;" in reference, "нет оффера с символом, требующим экранирования"


def test_active_reference_covers_whitespace_names():
    """Ради этого случая второй эталон и заведён."""
    reference = FIXTURE_ACTIVE.read_text(encoding="utf-8")
    assert re.search(r"<model> \S", reference), "нет названия с ведущим пробелом"
    assert re.search(r"\t</model>", reference), "нет названия с табуляцией на конце"


# ── Инварианты: лучше не собрать файл, чем собрать с дырой ──────────────────

def _ok_product(**over):
    base = {
        "merchantSku": "D844-0005",
        "name": "Мотыга",
        "brand": "barkhan",
        "price_num": 590,
        "stock": 64,
    }
    base.update(over)
    return base


def test_refuses_empty_catalog():
    """Пустой файл — это команда «снять с продажи всё»."""
    with pytest.raises(FeedSerializationError):
        generate_xml_feed("20186986", [])


@pytest.mark.parametrize("price", [0, -1, None, "", "не число"])
def test_refuses_bad_price(price):
    with pytest.raises(FeedSerializationError):
        generate_xml_feed("20186986", [_ok_product(price_num=price)])


@pytest.mark.parametrize("stock", [None, "", "много", -5])
def test_refuses_bad_stock(stock):
    with pytest.raises(FeedSerializationError):
        generate_xml_feed("20186986", [_ok_product(stock=stock)])


def test_refuses_offer_without_merchant_sku():
    """masterSku в поле sku — не замена артикулу продавца."""
    product = _ok_product()
    del product["merchantSku"]
    product["sku"] = "147776238"
    with pytest.raises(FeedSerializationError):
        generate_xml_feed("20186986", [product])


def test_refuses_duplicate_sku():
    with pytest.raises(FeedSerializationError):
        generate_xml_feed("20186986", [_ok_product(), _ok_product()])


def test_uses_merchant_sku_not_master_sku():
    xml = generate_xml_feed(
        "20186986", [_ok_product(sku="147776238")]
    ).decode("utf-8")
    assert '<offer sku="D844-0005">' in xml
    assert "147776238" not in xml


def test_synthesises_single_point_store_id():
    xml = generate_xml_feed("20186986", [_ok_product()]).decode("utf-8")
    assert 'storeId="20186986_001"' in xml


def test_explicit_point_store_id_wins():
    xml = generate_xml_feed(
        "20186986", [_ok_product(point_store_id="20186986_007")]
    ).decode("utf-8")
    assert 'storeId="20186986_007"' in xml


def test_stock_is_serialised_with_one_decimal():
    xml = generate_xml_feed("20186986", [_ok_product(stock=7)]).decode("utf-8")
    assert 'stockCount="7.0"' in xml


def test_escapes_special_characters_in_text():
    """В тексте экранируются только & < > — кавычка там валидна, и эталонный
    файл Kaspi оставляет её сырой (`<model>Хомут 1/2" - 3/4"...`)."""
    xml = generate_xml_feed(
        "20186986", [_ok_product(name='Щётка "Ёж" & Ко <2 шт>')]
    ).decode("utf-8")
    assert '<model>Щётка "Ёж" &amp; Ко &lt;2 шт&gt;</model>' in xml


def test_preserves_surrounding_whitespace_in_name():
    """Обрезка пробелов — тихое переименование чужой карточки, а не уборка."""
    xml = generate_xml_feed(
        "20186986", [_ok_product(name=" Barkhan Набор крючков\t")]
    ).decode("utf-8")
    assert "<model> Barkhan Набор крючков\t</model>" in xml


def test_whitespace_only_name_is_an_empty_tag():
    """Но строка из одних пробелов — это отсутствие названия, а не тег с пробелом."""
    xml = generate_xml_feed("20186986", [_ok_product(name="   ")]).decode("utf-8")
    assert "<model/>" in xml


def test_escapes_quote_in_attribute():
    """А вот в атрибуте кавычка обязана быть экранирована, иначе документ рвётся."""
    xml = generate_xml_feed(
        "20186986", [_ok_product(merchantSku='D844"0005')]
    ).decode("utf-8")
    assert '<offer sku="D844&quot;0005">' in xml
