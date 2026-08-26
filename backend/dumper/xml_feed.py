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
import re

# Городской код Алматы. У Бархана все 320 городов в allCityPrices несут ОДНУ
# и ту же цену (замерено 2026-08-23, офферов с разной ценой по городам — 0),
# и эталонный файл несёт ровно один <cityprice> с этим кодом.
DEFAULT_CITY_ID = "750000000"

XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = "http://kaspi.kz/kaspishopping.xsd"
CATALOG_NS = "kaspiShopping"


class FeedSerializationError(ValueError):
    """Оффер нельзя записать в фид, не соврав. Лучше не собрать файл вообще."""


# Символы, которые XML 1.0 §2.2 запрещает в документе В ПРИНЦИПЕ — их нельзя
# записать ни литералом, ни символьной ссылкой (`&#1;` невалиден так же, как
# сам байт). Табуляции, перевода строки и возврата каретки здесь НЕТ: они
# разрешены и с 2026-08-26 едут ссылкой, целыми. Это единственный настоящий
# «недостижимый фидом» класс артикулов.
_XML_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


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
    """
    Экранирование значения АТРИБУТА: то же плюс кавычка и запрещённые символы.

    Табуляция, перевод строки и возврат каретки едут ЛИТЕРАЛОМ, и Kaspi
    увидит на их месте пробел (XML 1.0 §3.3.3 — нормализация значения
    атрибута). Это не небрежность, а единственный доступный вариант.

    Обходной путь через символьную ссылку (`&#9;`) проверен на живой отправке
    2026-08-27 и НЕ РАБОТАЕТ. Спецификация на его стороне — для ссылки §3.3.3
    предписывает «append the referenced character», без замены, — но парсер
    Kaspi её всё равно не сопоставляет. Файл `6a8f391497dfba199f5ae991` нёс
    `sku="H477-0009&#9;"` и `sku="H477-0017&#9;"`, обе ссылки; вердикт —
    `FLUSHED, rejected 0, notMapped 2`, и ровно эти две карточки уехали в
    архив. Остальные 1098 сопоставились, цены и остатки не сдвинулись ни на
    одном.

    Отсюда вывод, который стоил двух карточек: **артикул с табуляцией фидом
    недостижим в принципе**, ни литералом, ни ссылкой. Ловит это не сериализатор,
    а `blockers` в плане — файл с таким артикулом не отправляется вообще.

    Края при этом Kaspi НЕ обрезает — тоже замер. В принятом файле
    `6a8c7b06b73ea421a0140dac` ехал ` S517-0005 ` с пробелами по обоим краям И
    отдельный `S517-0005`; обрезка схлопнула бы их в один, вместо этого
    `rejected: 0`. Пробел по краям безопасен, и трогать его нельзя.
    """
    text = _escape(value).replace('"', "&quot;")
    # Запрещённый символ вырезаем, а не роняем сборку: предпросмотр должен
    # собраться и назвать виновника, а отказ живёт в отправке (`send_plan`)
    # — ровно та же развилка, что и у `blockers` в плане. Молчаливой потери
    # тут нет: `feed_sku_roundtrip` вырезает то же самое, поэтому такой
    # артикул попадает в `blockers` и файл не уедет.
    return _XML_FORBIDDEN.sub("", text)


def unescape_attr(text) -> str:
    """
    Обратное к `_escape_attr`: значение атрибута из сырого текста файла.

    Нужно всем, кто читает наш собственный отправленный файл обратно (сверка
    после вердикта, поля идентичности).

    `&#9;`/`&#10;`/`&#13;` мы больше не пишем (см. `_escape_attr`), но разбор
    их оставлен намеренно: на диске лежат файлы, отправленные 2026-08-26,
    и сверка по ним должна читаться и сегодня.
    """
    return (
        str(text)
        .replace("&#9;", "\t")
        .replace("&#10;", "\n")
        .replace("&#13;", "\r")
        .replace("&quot;", '"')
        .replace("&gt;", ">")
        .replace("&lt;", "<")
        .replace("&amp;", "&")
    )


def xml_attr_value(raw) -> str:
    """
    Артикул, приведённый к виду, пригодному для СРАВНЕНИЯ с данными Kaspi.

    Табуляция схлопывается в пробел — ровно так её теряют обе стороны: и наш
    файл при нормализации значения атрибута, и сама Kaspi в собственной
    выгрузке каталога (в `ACTIVE.xml` те же офферы приходят как `H477-0009 `).
    Ключ сравнения обязан переживать обе формы, иначе сверка разойдётся на
    ровном месте.

    Края не трогаем: пробел по краям артикула Kaspi сохраняет (замер, см.
    `_escape_attr`), и `.strip()` здесь склеил бы два разных оффера в один.

    Это ключ ДЛЯ СОПОСТАВЛЕНИЯ, а не предсказание судьбы оффера. Что увидит
    Kaspi, приняв файл, отвечает `feed_sku_roundtrip`.
    """
    return str(raw).replace("\t", " ").replace("\n", " ").replace("\r", " ")


def feed_sku_roundtrip(raw) -> str:
    """
    Артикул ТАК, КАК ЕГО СОПОСТАВИТ KASPI, приняв наш файл.

    Три потери, все замеренные на живых отправках:

    · табуляция/перевод строки схлопываются в пробел (XML 1.0 §3.3.3).
      Обход символьной ссылкой `&#9;` НЕ работает — парсер Kaspi спеке тут не
      следует: файл `6a8f391497dfba199f5ae991` нёс обе ссылки, вердикт
      `notMapped: 2`, обе карточки в архив;
    · края обрезаются. Это доказано не догадкой, а тремя близнецами Бархана.
      Файл 24.08 объявил ` S517-0005 ` → 490, `S517-0005` → 1118,
      `S517-0005===` → 694. Последний JSON-пуш по всем трём был 16.08 с теми
      же значениями, после него — ни одного. Сегодня в каталоге:
      `S517-0005===` = 694 (своё), ` S517-0005 ` в архиве, а `S517-0005`
      несёт **490** вместо 1118. То есть Kaspi обрезала края, сопоставила
      запись с близнецом и записала в него ЧУЖУЮ цену, а оригинал, оставшись
      неупомянутым, сняла с продажи;
    · символы, запрещённые XML 1.0 §2.2, не записать никак — вырезаются.

    Обрезка здесь однажды уже стояла, и я убрал её 2026-08-26, решив по
    выгрузке каталога, что края выживают. Выгрузка показывала лишь то, что
    **каталог** различает ` X ` и `X`; различает ли их **импортёр** — вопрос
    другой, и ответ на него оказался «нет».

    Расхождение с исходным артикулом означает: фидом такой оффер недостижим,
    а если у него есть обрезанный близнец — отправка ещё и молча испортит
    близнеца. Файл не отправляем (`blockers`), пару ловит проверка коллизий.
    """
    return _XML_FORBIDDEN.sub("", xml_attr_value(raw)).strip()


# Как показать невидимый символ человеку. Кабинет Kaspi эти символы НЕ
# показывает и не даёт стереть: владелец Бархана 2026-08-24 не смог ни увидеть
# табуляцию в `H477-0009`, ни убрать пробелы в ` S517-0005 `. Поэтому наше
# сообщение о таком артикуле обязано печатать края явно — «почините артикул»
# без указания, что именно чинить, бесполезно.
_VISIBLE_EDGE = {
    "\t": "<TAB>",
    "\n": "<LF>",
    "\r": "<CR>",
    " ": "<ПРОБЕЛ>",
    "\xa0": "<NBSP>",
}


def describe_sku_defect(raw) -> str:
    """
    Артикул с подсвеченными краевыми невидимыми символами: `H477-0009<TAB>`.

    Подсвечиваются только КРАЯ: именно их Kaspi обрезает при приёме, и именно
    они решают судьбу оффера. Пробел в середине артикула безвреден, и метить
    его значило бы утопить настоящую проблему в шуме.
    """
    def mark(part: str) -> str:
        return "".join(_VISIBLE_EDGE.get(ch, repr(ch)) for ch in part)

    text = str(raw)
    head_len = len(text) - len(text.lstrip())
    body_end = len(text.rstrip())
    if body_end <= head_len:  # артикул целиком из пробелов
        return mark(text)
    return mark(text[:head_len]) + text[head_len:body_end] + mark(text[body_end:])


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
    """
    Текстовый тег. Пустое значение — самозакрывающийся: в эталоне 171 `<model/>`
    и 351 `<brand/>`, Kaspi это принимает.

    Значение пишется БЕЗ .strip(). Обрезка выглядит безобидной уборкой, но это
    правка чужих данных: в живом каталоге Бархана 6 карточек имеют ведущий
    пробел в названии, а `H477-0009` — табуляцию на конце, и в выгрузке Kaspi
    они сохранены дословно (сверено с ACTIVE от 2026-08-23, 1110 офферов).
    Обрезав, мы бы каждым фидом переименовывали карточки, которых не просили
    трогать. Пустоту при этом определяем по обрезанному значению: строка из
    одних пробелов — это `<model/>`, а не тег с пробелом внутри.
    """
    if value is None or str(value).strip() == "":
        return f"{indent}<{name}/>"
    return f"{indent}<{name}>{_escape(str(value))}</{name}>"


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
        # Сверяем по тому виду, который получит Kaspi. Раньше ключом был
        # `xml_attr_value`, потому что табуляция и пробел действительно
        # приезжали одинаковыми; теперь табуляция едет символьной ссылкой и
        # остаётся собой, так что это разные офферы и схлопывать их нельзя.
        key = feed_sku_roundtrip(sku)
        if key in seen:
            raise FeedSerializationError(
                f"дубль merchantSku в снимке: {sku!r} (Kaspi получит {key!r})"
            )
        seen.add(key)

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
