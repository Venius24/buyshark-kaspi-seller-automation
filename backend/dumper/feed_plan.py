"""
Dry-run прайс-фида: собрать файл и показать человеку, что он изменит.

Этап 3 гибридного демпинга. Здесь НЕТ и не должно появиться ни одного
пишущего запроса в Kaspi: всё, что модуль делает, — читает факт бесплатным
`bff/offer-view/list` (через снимок каталога), накладывает на него очередь
намерений и отдаёт получившийся файл вместе с диффом. Квота FILE/OFFER не
тратится, `upload` и `process` не вызываются.

Зачем отдельный этап, а не сразу отправка. Прайс-лист объявляет ВЕСЬ каталог,
и оффер, которого в файле нет, Kaspi снимает с продажи (HANDOFF.md §3.67).
Значит перед первой отправкой человек должен увидеть три числа и поверить им:

    изменится N · не изменится M · отсутствует в файле — 0

Третье число тут главное. Первые два — про пользу, третье — про то, что файл
не погасит каталог. Поэтому оно считается не «по построению» (мол, мы же
собирали файл из факта, откуда там пропажам взяться), а честно: готовый XML
парсится обратно, и множество его SKU сверяется с множеством SKU факта. Если
между сборкой и сериализацией оффер потеряется — узнаем здесь, а не в кабинете
владельца.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import datetime
import logging
import xml.etree.ElementTree as ET

from backend.dumper.feed_snapshot import CatalogSnapshot, build_catalog_snapshot
from backend.dumper.xml_feed import (
    CATALOG_NS,
    describe_sku_defect,
    feed_sku_roundtrip,
    generate_xml_feed,
)

logger = logging.getLogger(__name__)


class FeedPlanRefused(Exception):
    """План не сошёлся сам с собой — отправлять такой файл нельзя."""


@dataclass
class OfferDiff:
    """Одно поле одного оффера, которое файл изменит."""

    merchant_sku: str
    field: str  # "price" | "stock" | "available"
    current: Any
    planned: Any
    reason: str = ""
    priority: int = 3


@dataclass
class FeedPlan:
    snapshot: CatalogSnapshot
    offers: List[dict]
    xml: bytes
    diffs: List[OfferDiff]
    unchanged: int
    missing_from_file: List[str] = field(default_factory=list)
    # Артикулы, которые файл физически не может адресовать (краевые пробелы и
    # табы). Непустой список = отправлять этот файл нельзя ни при каких флагах,
    # см. feed_uploader.send_plan. Хранится как данные, а не как исключение,
    # чтобы предпросмотр показал их владельцу вместо того, чтобы упасть.
    blockers: List[str] = field(default_factory=list)
    orphan_intents: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # id принятого Kaspi файла, из которого взяты <model>/<brand>. None = поля
    # идентичности не сверены, и отправлять такой файл нельзя (см. feed_uploader).
    identity_source: Optional[str] = None
    identity_unknown: int = 0
    built_at: Optional[datetime.datetime] = None

    @property
    def changed_offers(self) -> int:
        """Офферов (не полей!) с хотя бы одним изменением."""
        return len({d.merchant_sku for d in self.diffs})

    @property
    def total(self) -> int:
        return len(self.offers)


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_intent(product: dict, intent: dict):
    """
    Наложить намерение на фактический оффер.

    Возвращает (оффер для файла, список расхождений). Оффер копируется:
    снимок — это факт, и портить его наложением плана нельзя, иначе следующая
    сверка будет сравнивать план с планом.
    """
    planned = dict(product)
    diffs: List[OfferDiff] = []
    sku = str(product.get("merchantSku"))
    reason = intent.get("reason", "")
    priority = intent.get("priority", 3)

    want_price = _as_int(intent.get("price"))
    if want_price is not None:
        have = _as_int(product.get("price_num"))
        if have != want_price:
            planned["price_num"] = want_price
            diffs.append(OfferDiff(sku, "price", have, want_price, reason, priority))

    want_stock = _as_int(intent.get("stock"))
    if want_stock is not None:
        have_stock = product.get("stock")
        try:
            have_f = float(have_stock)
        except (TypeError, ValueError):
            have_f = None
        if have_f is None or float(want_stock) != have_f:
            planned["stock"] = want_stock
            diffs.append(OfferDiff(sku, "stock", have_stock, want_stock, reason, priority))

    want_avail = intent.get("available")
    if want_avail is not None:
        have_avail = bool(product.get("available", True))
        if bool(want_avail) != have_avail:
            planned["available"] = bool(want_avail)
            diffs.append(OfferDiff(sku, "available", have_avail, bool(want_avail), reason, priority))

    return planned, diffs


def _skus_in_xml(xml_bytes: bytes) -> List[str]:
    """Разобрать готовый файл обратно и вынуть sku всех офферов."""
    root = ET.fromstring(xml_bytes.decode("utf-8"))
    return [
        offer.attrib["sku"]
        for offer in root.iter("{%s}offer" % CATALOG_NS)
        if "sku" in offer.attrib
    ]


def build_feed_plan(
    token: str,
    store_id: str,
    baseline_count: Optional[int] = None,
    intents: Optional[Dict[str, dict]] = None,
    generated_at: Optional[datetime.datetime] = None,
    identity: Optional[Dict[str, dict]] = None,
    identity_source: Optional[str] = None,
    use_accepted_identity: bool = True,
) -> FeedPlan:
    """
    Собрать файл и дифф, ничего никуда не отправляя.

    intents — очередь намерений {merchantSku: {price, stock, available, ...}}.
    По умолчанию читается из БД (`offer_sync.pending_intents`); передаётся явно
    в тестах и когда нужно проиграть «а что будет, если».

    Бросает FeedSnapshotRefused, если факт нельзя выкачать полностью, и
    FeedPlanRefused, если готовый файл не совпал с фактом по составу офферов.
    """
    snapshot = build_catalog_snapshot(token, store_id, baseline_count=baseline_count)

    if intents is None:
        from backend.services import offer_sync

        intents = offer_sync.pending_intents(store_id)

    fact_skus = {str(p.get("merchantSku")) for p in snapshot.offers}
    offers: List[dict] = []
    diffs: List[OfferDiff] = []
    for product in snapshot.offers:
        intent = intents.get(str(product.get("merchantSku")))
        if not intent:
            offers.append(product)
            continue
        planned, offer_diffs = _apply_intent(product, intent)
        offers.append(planned)
        diffs.extend(offer_diffs)

    # Поля идентичности — из файла, который Kaspi сама приняла и сама отдаёт
    # обратно. Из BFF их брать нельзя: там бренд лежит display-именем
    # («Волат»), а в фиде он слаг («volat»), и вывести одно из другого
    # невозможно. Подробности — в feed_identity.
    identity_stats = None
    if identity is None and use_accepted_identity:
        from backend.dumper.feed_identity import fetch_accepted_identity

        identity_source, identity = fetch_accepted_identity(store_id, snapshot.merchant_id)
    if identity:
        from backend.dumper.feed_identity import apply_identity

        offers, identity_stats = apply_identity(offers, identity)

    # Намерения по SKU, которых в каталоге нет. Файлом их не выразить: оффера,
    # отсутствующего в факте, в файле тоже не будет, и намерение просто
    # потеряется. Это не повод не собирать файл, но человек должен знать.
    orphans = sorted(set(intents) - fact_skus)

    warnings: List[str] = list(snapshot.notes)
    if identity_stats is None:
        warnings.append(
            "поля идентичности НЕ сверены с принятым файлом — названия и бренды пришли из BFF "
            "и почти наверняка разойдутся со слагами Kaspi; отправка заблокирована"
        )
    elif identity_stats["unknown"]:
        warnings.append(
            "офферов вне принятого файла: {} — уйдут с пустыми <model/> и <brand/> "
            "(напр. {})".format(len(identity_stats["unknown"]), identity_stats["unknown"][:5])
        )
    if orphans:
        warnings.append(
            "намерений по SKU вне каталога: {} — файлом они не поедут (напр. {})".format(
                len(orphans), orphans[:5]
            )
        )
    withdrawals = [d for d in diffs if d.field == "available" and not d.planned]
    if withdrawals:
        warnings.append(
            "снятий с продажи через фид: {} — этот путь ещё не проверялся живьём, "
            "снимать надёжнее JSON-ом".format(len(withdrawals))
        )

    # Схлопывание: два разных артикула станут в файле одним оффером — второй
    # просто исчезнет. Проверяем ДО сборки, чтобы назвать виновную пару
    # поимённо (сериализатор тоже это ловит, но он видит лишь второй SKU).
    #
    # Ключ здесь — `feed_sku_roundtrip`, то есть то, что Kaspi реально получит,
    # а не `xml_attr_value`. Пока табуляция ехала литералом, эти две функции
    # совпадали; теперь она едет символьной ссылкой и доезжает целой, так что
    # `H477-0009<TAB>` и `H477-0009<ПРОБЕЛ>` — два разных оффера, а не пара.
    normalized = {}
    for raw in fact_skus:
        normalized.setdefault(feed_sku_roundtrip(raw), []).append(raw)
    collisions = {k: v for k, v in normalized.items() if len(v) > 1}
    if collisions:
        raise FeedPlanRefused(
            "разные merchantSku схлопываются в один при нормализации атрибута: "
            "{}".format({k: [repr(x) for x in v] for k, v in list(collisions.items())[:3]})
        )

    # Артикулы, которые файл физически не может назвать. С 2026-08-26 таких
    # почти не бывает: табуляция и переводы строк пишутся символьной ссылкой
    # (`&#9;`) и доезжают до Kaspi целыми, а края она не обрезает — проверено
    # на принятом файле 6a8c7b06b73ea421a0140dac, где ` S517-0005 ` с пробелами
    # по краям сматчился при `rejected: 0`.
    #
    # Прежняя версия этой проверки исходила из обратного и записывала в блокеры
    # любой краевой пробел. На Бархане это давало четыре «битых» артикула,
    # отказ обслуживать весь магазин и приговор «чинить только в кабинете» —
    # где артикул как раз и не редактируется. Настоящий блокер остался один:
    # символ, запрещённый в XML 1.0 §2.2, — его не записать никак.
    #
    # Почему предупреждение, а не исключение: dry-run должен собираться и
    # называть виновников поимённо. Отказ живёт в отправке (send_plan).
    blockers = sorted(s for s in fact_skus if feed_sku_roundtrip(s) != s)
    if blockers:
        warnings.append(
            "{} артикулов содержат символы, запрещённые в XML — файл не может на них "
            "сослаться, и Kaspi снимет их с продажи. ОТПРАВКА ФИДА ЗАБЛОКИРОВАНА: {}".format(
                len(blockers), [describe_sku_defect(s) for s in blockers[:5]]
            )
        )

    xml = generate_xml_feed(snapshot.merchant_id, offers, generated_at=generated_at)

    # Инвариант полноты, посчитанный по готовому файлу, а не по намерению его
    # собрать. Пропажа здесь = карточки, которые Kaspi погасит.
    file_skus = set(_skus_in_xml(xml))
    missing = sorted(set(normalized) - file_skus)
    if missing:
        raise FeedPlanRefused(
            "в файле не хватает {} офферов из факта — Kaspi снимет их с продажи "
            "(напр. {})".format(len(missing), missing[:5])
        )
    extra = sorted(file_skus - set(normalized))
    if extra:
        raise FeedPlanRefused(
            "в файле есть {} офферов, которых нет в факте — состав файла разошёлся "
            "со снимком (напр. {})".format(len(extra), extra[:5])
        )

    plan = FeedPlan(
        snapshot=snapshot,
        offers=offers,
        xml=xml,
        diffs=diffs,
        unchanged=len(offers) - len({d.merchant_sku for d in diffs}),
        missing_from_file=[],
        blockers=blockers,
        orphan_intents=orphans,
        warnings=warnings,
        identity_source=identity_source,
        identity_unknown=len(identity_stats["unknown"]) if identity_stats else 0,
        built_at=datetime.datetime.now(),
    )
    logger.info(
        "[FeedPlan] Файл собран: всего %s, изменится %s, не изменится %s, отсутствует 0. %s",
        plan.total, plan.changed_offers, plan.unchanged, "; ".join(warnings) or "замечаний нет",
    )
    return plan


def render_plan_report(plan: FeedPlan, limit: int = 25) -> str:
    """Дифф в человекочитаемом виде — то, на что смотрит владелец перед отправкой."""
    lines = [
        "ПРАЙС-ЛИСТ — ПРЕДПРОСМОТР (ничего не отправлено)",
        "  магазин {}, мерчант {}".format(plan.snapshot.store_id, plan.snapshot.merchant_id),
        "  снимок от {:%Y-%m-%d %H:%M:%S}, размер файла {} байт".format(
            plan.snapshot.taken_at, len(plan.xml)
        ),
        "",
        "  всего офферов в файле : {}".format(plan.total),
        "  изменится             : {}".format(plan.changed_offers),
        "  не изменится          : {}".format(plan.unchanged),
        "  отсутствует в файле   : {}".format(len(plan.missing_from_file)),
        "  снято с продажи (архив, в файл не идёт): {}".format(plan.snapshot.archived_count),
        "  названия и бренды   : {}".format(
            "из принятого файла {} (вне файла — {})".format(
                plan.identity_source, plan.identity_unknown)
            if plan.identity_source else "НЕ СВЕРЕНЫ — отправка заблокирована"
        ),
    ]
    if plan.warnings:
        lines.append("")
        lines.append("  ВНИМАНИЕ:")
        lines.extend("    · {}".format(w) for w in plan.warnings)

    if plan.blockers:
        # Печатаем ПОИМЁННО и с подсветкой краёв: в кабинете этих символов не
        # видно, так что список артикулов без разметки владельцу бесполезен.
        lines.append("")
        lines.append("  ОТПРАВКА ЗАБЛОКИРОВАНА — артикулы с невидимыми символами:")
        for sku in plan.blockers:
            lines.append("    {}".format(describe_sku_defect(sku)))
        lines.append(
            "    Файл не может сослаться на такой оффер: Kaspi обрежет края и снимет"
        )
        lines.append(
            "    его с продажи. Магазин при этом продолжает работать по JSON-пути."
        )

    if plan.diffs:
        lines.append("")
        lines.append("  Что именно изменится:")
        shown = sorted(plan.diffs, key=lambda d: (d.priority, d.merchant_sku))[:limit]
        for d in shown:
            lines.append(
                "    {:<20} {:<9} {} -> {}   [{}, приоритет {}]".format(
                    d.merchant_sku, d.field, d.current, d.planned,
                    d.reason or "без причины", d.priority,
                )
            )
        if len(plan.diffs) > limit:
            lines.append("    … и ещё {} изменений".format(len(plan.diffs) - limit))
    else:
        lines.append("")
        lines.append(
            "  Изменений нет — файл идемпотентен (это нормально и даже желательно"
            " для первой отправки)."
        )
    return "\n".join(lines)
