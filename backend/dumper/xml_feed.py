"""
Сериализатор прайс-листа Kaspi (kaspi_catalog XML).

⚠️ ФАЙЛ — ЭТО ДЕКЛАРАЦИЯ ВСЕГО КАТАЛОГА, А НЕ ПАТЧ.
Оффер, которого в файле нет, Kaspi снимает с продажи с причиной «не указан в
прайс-листе». Именно так у владельца однажды слегло ~798 карточек: файл на два
SKU приняли, обе цены поменялись, остальной каталог погас (HANDOFF.md §3.67).
Поэтому здесь нет и не должно быть режима «частичный фид»: полноту гарантирует
сборщик снимка (`backend/dumper/feed_snapshot.py`), а этот модуль отказывается
сериализовать оффер, про который чего-то не знает.

Схема сверена с ЭТАЛОНОМ — реальным файлом, который Kaspi приняла у Бархана
15.08.2026 (`GET /pricefeed/upload/merchant/files/{id}/download`,
`processedOffersCount: 1153, rejectedOffersCount: 0`). Прошлая версия этого
модуля была весенней заготовкой и расходилась с эталоном по КАЖДОМУ пункту:
namespace `http://kaspi.kz/kaspicatalog` вместо `kaspiShopping`, `<company>` с
названием фирмы вместо merchant_id, цена в `<price>` вместо
`<cityprices><cityprice>`, целый `stock=` вместо дробного `stockCount=`, не было
`preOrder`, бренд был захардкожен строкой "Barkhan".
"""
from typing import List, Optional
import datetime

# Городской код Алматы. У Бархана все 320 городов в allCityPrices несут ОДНУ
# и ту же цену (замерено 2026-08-23, офферов с разной ценой по городам — 0),
# и эталонный файл несёт ровно один <cityprice> с этим кодом.
DEFAULT_CITY_ID = "750000000"

XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = "http://kaspi.kz/kaspishopping.xsd"
CATALOG_NS = "kaspiShopping"


class FeedSerializationError(ValueError):
    """Оффер нельзя записать в фид, не соврав. Лучше не собрать файл вообще."""


def _escape(text: str) -> str:
    """Экранирование ТЕКСТОВОГО узла: только &, < и >.

    Кавычку здесь не трогаем — в тексте она валидна, и эталонный файл Kaspi
    оставляет её как есть (`<model>Хомут 1/2" - 3/4"...</model>`). Экранировать
    её сверх нормы значило бы разойтись со схемой, которую Kaspi уже приняла.
    """
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _escape_attr(value: str) -> str:
    """Экранирование значения АТРИБУТА: то же плюс кавычка."""
    return _escape(value).replace('"', "&quot;")


def _point_store_id(merchant_id: str, product: dict) -> str:
    """
    storeId точки продаж. У Бархана точка одна — `20186986_001`, и все 1119
    офферов лежат ровно на ней. Синтез `{merchant_id}_001` для него верен, но
    это совпадение, а не правило: мультиточечный мерчант так сломается, поэтому
    настоящий storeId, если он есть в данных оффера, всегда в приоритете.
    """
    explicit = product.get("point_store_id") or product.get("storeId")
    if explicit:
        return str(explicit)
    return f"{merchant_id}_001"


def _offer_sku(product: dict) -> str:
    """
    В фид идёт merchantSku (артикул продавца: `D844-0005`), а НЕ код мастер-
    карточки Kaspi (`147776238`). В нашем кэше ассортимента поле `sku` — это как
    раз masterSku, так что взять его по имени было бы естественной и очень
    дорогой ошибкой: офферы не смапятся, а всё, что не смапилось, погаснет.
    """
    merchant_sku = product.get("merchantSku")
    if merchant_sku:
        return str(merchant_sku)
    raise FeedSerializationError(
        f"у оффера нет merchantSku (sku={product.get('sku')!r}) — "
        "в фид нельзя: без артикула продавца оффер не смапится"
    )


def _offer_price(product: dict) -> int:
    """
    Цена. None и 0 — принципиально разные вещи только для чтения; для записи
    оба означают «мы не знаем», а ноль в прайс-листе выбивает оффер из него
    (см. HANDOFF.md §3.40). Поэтому любая неположительная цена — отказ.
    """
    raw = product.get("price_num")
    try:
        price = int(raw)
    except (TypeError, ValueError):
        raise FeedSerializationError(
            f"нечитаемая цена у {product.get('merchantSku') or product.get('sku')!r}: {raw!r}"
        )
    if price <= 0:
        raise FeedSerializationError(
            f"неположительная цена у {product.get('merchantSku') or product.get('sku')!r}: {price}"
        )
    return price


def _offer_stock(product: dict) -> float:
    raw = product.get("stock")
    try:
        stock = float(raw)
    except (TypeError, ValueError):
        raise FeedSerializationError(
            f"нечитаемый остаток у {product.get('merchantSku') or product.get('sku')!r}: {raw!r}"
        )
    if stock < 0:
        raise FeedSerializationError(
            f"отрицательный остаток у {product.get('merchantSku') or product.get('sku')!r}: {stock}"
        )
    return stock


def _tag(name: str, value: Optional[str], indent: str) -> str:
    """Пустое значение — самозакрывающийся тег: в эталоне 171 `<model/>` и
    351 `<brand/>`, Kaspi это принимает."""
    if value is None or str(value).strip() == "":
        return f"{indent}<{name}/>"
    return f"{indent}<{name}>{_escape(str(value).strip())}</{name}>"


def generate_xml_feed(
    merchant_id: str,
    products: List[dict],
    generated_at: Optional[datetime.datetime] = None,
    city_id: str = DEFAULT_CITY_ID,
) -> bytes:
    """
    Собрать прайс-лист kaspi_catalog из ПОЛНОГО списка офферов.

    products — снимок каталога, каждый элемент как в
    `KaspiAPI.sync_merchant_assortment`: merchantSku, name, price_num, stock,
    brand, плюс необязательные available / point_store_id.

    Бросает FeedSerializationError на первом же оффере, который нельзя записать
    честно. Это намеренно: пропустить такой оффер = снять его с продажи, а
    отдать файл с дырой хуже, чем не отдать файл.
    """
    if not products:
        raise FeedSerializationError(
            "пустой список офферов — такой файл снимет с продажи весь каталог"
        )

    now = generated_at or datetime.datetime.now()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<kaspi_catalog xmlns="{CATALOG_NS}" xmlns:xsi="{XSI_NS}"'
        f' xsi:schemaLocation="{SCHEMA_LOCATION}" date="{now.strftime("%Y-%m-%d %H:%M")}">',
        f"    <company>{_escape(merchant_id)}</company>",
        f"    <merchantid>{_escape(merchant_id)}</merchantid>",
        "    <offers>",
    ]

    seen = set()
    for product in products:
        sku = _offer_sku(product)
        if sku in seen:
            raise FeedSerializationError(f"дубль merchantSku в снимке: {sku!r}")
        seen.add(sku)

        price = _offer_price(product)
        stock = _offer_stock(product)
        # `available` по умолчанию «да»: снимок собирается из активной выборки
        # BFF. Явное False уважаем — но фидом снимать с продажи мы пока не
        # умеем и не проверяли, эталон целиком состоит из available="yes".
        available = "yes" if product.get("available", True) else "no"

        lines.append(f'        <offer sku="{_escape_attr(sku)}">')
        lines.append(_tag("model", product.get("name"), " " * 12))
        lines.append(_tag("brand", product.get("brand"), " " * 12))
        lines.append("            <availabilities>")
        lines.append(
            f'                <availability available="{available}"'
            f' storeId="{_escape_attr(_point_store_id(merchant_id, product))}"'
            f' preOrder="0" stockCount="{stock:.1f}"/>'
        )
        lines.append("            </availabilities>")
        lines.append("            <cityprices>")
        lines.append(
            f'                <cityprice cityId="{_escape_attr(city_id)}">{price}</cityprice>'
        )
        lines.append("            </cityprices>")
        lines.append("        </offer>")

    lines.append("    </offers>")
    lines.append("</kaspi_catalog>")
    return ("\n".join(lines) + "\n").encode("utf-8")
