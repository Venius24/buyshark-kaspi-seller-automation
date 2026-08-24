"""
Тесты dry-run прайс-фида (этап 3).

Проверяется два свойства. Первое — дифф честный: показывает ровно те поля,
которые файл изменит, и не показывает те, что совпадают с фактом. Второе, и
более важное, — инвариант «отсутствует в файле — 0» реально считается по
готовому XML, а не декларируется. Поэтому здесь есть тест, который специально
портит сериализатор так, чтобы оффер потерялся, и требует отказа.
"""
import datetime

import pytest

from backend.dumper import feed_plan, feed_snapshot
from backend.dumper.feed_plan import (
    FeedPlanRefused,
    build_feed_plan,
    render_plan_report,
)


def _offer(sku, price=590, stock=64.0, available=True):
    return {
        "merchantSku": sku,
        "sku": "147776238",
        "name": "Товар {}".format(sku),
        "brand": "barkhan",
        "price_num": price,
        "stock": stock,
        "available": available,
        "status": "Активен" if available else "Архив",
    }


@pytest.fixture
def catalog(monkeypatch):
    """Факт на Kaspi: 20 офферов по 590 ₸ и 64 штуки."""
    state = {"products": [_offer("SKU-{:03d}".format(i)) for i in range(20)]}

    monkeypatch.setattr(
        feed_snapshot.KaspiAPI,
        "sync_merchant_assortment",
        staticmethod(lambda token, store_id=None, strict=False: list(state["products"])),
    )
    monkeypatch.setattr(
        feed_snapshot.KaspiAPI, "get_merchant_id", staticmethod(lambda token: "20186986")
    )
    return state


def _plan(intents=None, **kw):
    kw.setdefault("use_accepted_identity", False)
    return build_feed_plan("tok", "store-1", baseline_count=20, intents=intents or {}, **kw)


def test_no_intents_means_idempotent_file(catalog):
    """Пустая очередь — файл повторяет факт. Именно этим и проверяется транспорт."""
    plan = _plan()
    assert plan.total == 20
    assert plan.changed_offers == 0
    assert plan.unchanged == 20
    assert plan.missing_from_file == []


def test_price_intent_shows_up_in_diff(catalog):
    plan = _plan({"SKU-003": {"price": 550, "reason": "dump:MATCH_FIRST", "priority": 1}})
    assert plan.changed_offers == 1
    assert plan.unchanged == 19
    d = plan.diffs[0]
    assert (d.merchant_sku, d.field, d.current, d.planned) == ("SKU-003", "price", 590, 550)
    assert d.priority == 1


def test_intent_equal_to_fact_is_not_a_change(catalog):
    """Намерение «поставить то, что уже стоит» не должно попадать в дифф."""
    plan = _plan({"SKU-003": {"price": 590, "stock": 64}})
    assert plan.diffs == []
    assert plan.unchanged == 20


def test_one_offer_two_fields_counts_as_one_changed_offer(catalog):
    plan = _plan({"SKU-005": {"price": 500, "stock": 3}})
    assert len(plan.diffs) == 2
    assert plan.changed_offers == 1
    assert plan.unchanged == 19


def test_planned_values_reach_the_file(catalog):
    plan = _plan({"SKU-007": {"price": 499, "stock": 2}})
    xml = plan.xml.decode("utf-8")
    assert "<cityprice cityId=\"750000000\">499</cityprice>" in xml
    assert "stockCount=\"2.0\"" in xml


def test_fact_is_not_mutated_by_the_plan(catalog):
    """Снимок — это факт; наложение плана поверх него превратило бы следующую
    сверку в сравнение плана с планом."""
    plan = _plan({"SKU-001": {"price": 100}})
    fact = {p["merchantSku"]: p for p in plan.snapshot.offers}
    assert fact["SKU-001"]["price_num"] == 590


def test_intent_for_unknown_sku_is_flagged_not_silently_dropped(catalog):
    plan = _plan({"НЕТ-ТАКОГО": {"price": 100}})
    assert plan.orphan_intents == ["НЕТ-ТАКОГО"]
    assert any("вне каталога" in w for w in plan.warnings)
    assert plan.total == 20  # но файл всё равно полный


def test_withdrawal_via_feed_is_warned_about(catalog):
    plan = _plan({"SKU-002": {"available": False}})
    assert any("снятий с продажи" in w for w in plan.warnings)


def test_every_fact_offer_is_in_the_file(catalog):
    plan = _plan({"SKU-004": {"price": 501}})
    skus = set(feed_plan._skus_in_xml(plan.xml))
    assert skus == {p["merchantSku"] for p in plan.snapshot.offers}


def test_refuses_when_an_offer_falls_out_of_the_file(catalog, monkeypatch):
    """Главный тест этапа: если между снимком и файлом оффер потеряется —
    отказ, а не тихий файл, который снимет карточку с продажи."""
    real = feed_plan.generate_xml_feed

    def lossy(merchant_id, products, **kw):
        return real(merchant_id, products[:-1], **kw)

    monkeypatch.setattr(feed_plan, "generate_xml_feed", lossy)
    with pytest.raises(FeedPlanRefused) as e:
        _plan()
    assert "не хватает 1" in str(e.value)


def test_refuses_when_the_file_grows_an_extra_offer(catalog, monkeypatch):
    real = feed_plan.generate_xml_feed

    def bloated(merchant_id, products, **kw):
        return real(merchant_id, list(products) + [_offer("ЛИШНИЙ")], **kw)

    monkeypatch.setattr(feed_plan, "generate_xml_feed", bloated)
    with pytest.raises(FeedPlanRefused) as e:
        _plan()
    assert "которых нет в факте" in str(e.value)


def test_snapshot_refusal_propagates(catalog):
    """Неполный факт — не повод строить план на нём."""
    catalog["products"] = [_offer("SKU-000")]
    with pytest.raises(feed_snapshot.FeedSnapshotRefused):
        _plan()


def test_report_shows_the_three_numbers(catalog):
    plan = _plan({"SKU-003": {"price": 550, "reason": "dump:MATCH_FIRST", "priority": 1}})
    text = render_plan_report(plan)
    assert "всего офферов в файле : 20" in text
    assert "изменится             : 1" in text
    assert "не изменится          : 19" in text
    assert "отсутствует в файле   : 0" in text
    assert "SKU-003" in text and "590 -> 550" in text


def test_report_says_idempotent_when_nothing_changes(catalog):
    assert "идемпотентен" in render_plan_report(_plan())


def test_generated_at_is_honoured(catalog):
    stamp = datetime.datetime(2026, 8, 23, 12, 30)
    plan = _plan(generated_at=stamp)
    assert "date=\"2026-08-23 12:30\"" in plan.xml.decode("utf-8")


def test_intents_are_read_from_db_by_default(catalog, monkeypatch):
    """Без явного intents план должен брать реальную очередь, а не пустоту."""
    from backend.services import offer_sync

    monkeypatch.setattr(
        offer_sync, "pending_intents", lambda store_id: {"SKU-009": {"price": 111}}
    )
    plan = build_feed_plan("tok", "store-1", baseline_count=20, use_accepted_identity=False)
    assert plan.changed_offers == 1
    assert plan.diffs[0].planned == 111


# ── Пробельные артикулы: живой случай Бархана (2026-08-24) ──────────────────

def test_tab_in_sku_refuses_the_whole_plan(catalog):
    """
    Раньше здесь утверждалось обратное: раз XML-парсер превращает таб в пробел
    ровно как в выгрузке самой Kaspi, значит оффер доедет. Живая загрузка
    2026-08-24 это опровергла — Kaspi обрезает края УЖЕ ПОСЛЕ нормализации,
    ищет `H477-0009`, такого артикула в каталоге нет, и оба оффера ушли в архив
    как отсутствующие в прайс-листе (`notMapped: 2`). Собирать такой файл
    нельзя.
    """
    catalog["products"] = (
        [_offer("SKU-{:03d}".format(i)) for i in range(18)]
        + [_offer("H477-0009\t"), _offer("H477-0017\t")]
    )
    with pytest.raises(FeedPlanRefused) as e:
        _plan()
    assert "не переживают приём файла" in str(e.value)


def test_leading_and_trailing_spaces_in_sku_refuse_too(catalog):
    """` S517-0005 ` — реальный артикул, и его Kaspi обрезает так же."""
    catalog["products"] = (
        [_offer("SKU-{:03d}".format(i)) for i in range(19)] + [_offer(" S517-0005 ")]
    )
    with pytest.raises(FeedPlanRefused) as e:
        _plan()
    assert "S517-0005" in str(e.value)


def test_refuses_when_two_skus_collapse_into_one(catalog):
    """А вот схлопывание разных артикулов в один — настоящая пропажа оффера."""
    catalog["products"] = (
        [_offer("SKU-{:03d}".format(i)) for i in range(18)]
        + [_offer("H477-0009\t"), _offer("H477-0009 ")]
    )
    with pytest.raises(FeedPlanRefused) as e:
        _plan()
    assert "схлопываются" in str(e.value)


def test_refuses_skus_that_cannot_survive_kaspi_ingest(monkeypatch):
    """
    Артикул с хвостовым табом уезжает в файл пробелом, Kaspi его обрезает и не
    находит в каталоге — оффер гаснет как «отсутствующий в прайс-листе».
    Загрузка 2026-08-24 сняла так H477-0009 и H477-0017; молча — недопустимо.
    """
    from backend.dumper.xml_feed import feed_sku_roundtrip

    assert feed_sku_roundtrip("H477-0009\t") == "H477-0009"
    assert feed_sku_roundtrip(" S517-0005 ") == "S517-0005"
    assert feed_sku_roundtrip("D844-0005") == "D844-0005"


def test_clean_sku_roundtrips_unchanged():
    from backend.dumper.xml_feed import feed_sku_roundtrip

    for sku in ("D844-0026-1", "198780339", "T114-0022-3-BL", "K471-0008-01"):
        assert feed_sku_roundtrip(sku) == sku
