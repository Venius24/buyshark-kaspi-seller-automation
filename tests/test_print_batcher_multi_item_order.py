"""
Сборный заказ и штамп Мастер-Группы.

Случай с прода (Бархан, заказ 1078201654, 17.09.2026): три товара, артикулы
H477-0301 / H422-0003-1 / H477-0006. Печать брала ПЕРВЫЙ артикул, он ни в
одной группе не числился, после чего штамп угадывался по названию — а
extract_product_name читает лишь первую строчку состава накладной. В итоге на
посылке со стойкой на 13 шаров стоял штамп «H422-0002 7 палочки», найденный по
нечёткому совпадению названия. Второй артикул заказа дал бы верную группу.
"""
import io
import zipfile

import fitz
import pytest

from backend.services import print_batcher_service as pb


_CYR_FONT = pb._resolve_stamp_fonts()[1]
pytestmark = pytest.mark.skipif(
    not _CYR_FONT, reason="в системе нет кириллического TTF — накладную не подделать"
)

# Имена ровно как на накладной Kaspi: витринные, с артикулом продавца внутри
# лишь иногда. Первая строчка — та, что вычитывает extract_product_name.
WAYBILL_LINES = [
    "AAT Стойка для шаров 1 шт",
    "H477-0006 Набор воздушных шаров Barkhan для праздника, 10 шт",
    "Набор воздушных шаров Barkhan универсальный 50 шт, белый",
]
ORDER_CODE = "1078201654"
ORDER_SKUS = {ORDER_CODE: ["H477-0301", "H422-0003-1", "H477-0006"]}

STOJKA_GROUP = {
    "group_id": "g-stojka",
    "group_name": "H422-0003 Стойка на 13 шаров",
    "linked_skus": ["H422-0003", "H422-0003-1", "H422-0003-2"],
    "is_active": True,
}
# Та самая группа-приманка: в её привязках лежит товар с названием «Стойка для
# шаров», из-за которого нечёткий матч и уводил штамп сюда.
PALOCHKI_GROUP = {
    "group_id": "g-palochki",
    "group_name": "H422-0002 7 палочки",
    "linked_skus": ["H422-0002", "141895753", "140551501", "109403754"],
    "is_active": True,
}
# Каталог нужен не декорацией: веса токенов (IDF) считаются по нему, и на
# каталоге из одной карточки порог схожести не берётся ни у чего. Состав —
# как у настоящей группы Бархана, плюс посторонние товары для фона.
PALOCHKI_PRODUCTS = [
    {"sku": "141895753", "name": "Стойка для шаров"},
    {"sku": "140551501", "name": "Палочки для воздушных шаров 7 шт"},
    {"sku": "109403754", "name": "Стойка на 7 шаров, 70см, пластик"},
    {"sku": "142645449", "name": "Кружка Barkhan T114-0002 1 шт"},
    {"sku": "106985192", "name": "Фляжка 19х9.5см, 520мл, нерж. сталь"},
    {"sku": "102926800", "name": "Термометр для воды бытовой"},
    {"sku": "145233543", "name": "Дозатор для жидкого мыла"},
    {"sku": "119904850", "name": "Мыльница дорожная 1 шт"},
    {"sku": "143829775", "name": "Наклейка декоративная 10x10 см"},
    {"sku": "197547443", "name": "Лента-липучка для подвязки растений"},
]


def _waybill() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    page.insert_text((20, 40), ORDER_CODE, fontsize=11, fontname="cyr", fontfile=_CYR_FONT)
    y = 70
    for line in WAYBILL_LINES:
        page.insert_text((20, y), f"{line} . . . . . . . . . .  1 шт.", fontsize=7,
                         fontname="cyr", fontfile=_CYR_FONT)
        y += 12
    page.insert_text((20, 300), "Астана", fontsize=9, fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((20, 390), "план дата доставки:", fontsize=7, fontname="cyr", fontfile=_CYR_FONT)
    data = doc.tobytes()
    doc.close()
    return data


def _archive() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"KASPI_SHOP-{ORDER_CODE}.pdf", _waybill())
    return buf.getvalue()


@pytest.fixture(autouse=True)
def clean_history(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "HISTORY_FILE_PATH", tmp_path / "printed_history.json")


@pytest.fixture
def prod_store(monkeypatch):
    """Группы и каталог магазина так, как их грузит process_zip_or_pdfs."""
    def _apply(groups, products=()):
        import backend.state_helpers as sh
        monkeypatch.setattr(sh, "_load_master_stock_groups",
                            lambda store_id: {g["group_id"]: g for g in groups}, raising=False)
        monkeypatch.setattr(sh, "_load_products", lambda store_id: list(products), raising=False)
        monkeypatch.setattr(sh, "_load_product_tags", lambda store_id, skus=None: {}, raising=False)
        monkeypatch.setattr(sh, "_load_store_categories", lambda store_id: {}, raising=False)
    return _apply


def _run(**kwargs):
    params = dict(
        file_bytes=_archive(),
        original_filename="waybill.zip",
        sort_preset="master_group",
        auto_rotate=True,
        express_mode="ignore",
        output_layout="thermal_75_120",
        store_id="store-1",
        deduplicate_history=False,
        filter_duplicates=False,
        order_skus=ORDER_SKUS,
    )
    params.update(kwargs)
    return pb.process_zip_or_pdfs(**params)


def _batch_text(manifest):
    path = pb.get_batch_file_path(manifest["batch_id"], "THERMAL_75x120")
    doc = fitz.open(str(path))
    text = "\n".join(p.get_text() for p in doc)
    doc.close()
    # Название группы в боксе переносится по словам, поэтому склеиваем строки:
    # иначе «Стойка на 13 шаров» не найти подстрокой.
    return " ".join(pb.normalize_waybill_text(text).split())


def test_group_is_found_by_any_sku_of_the_order(prod_store):
    """Группа берётся по второму артикулу заказа, а не сдаётся на первом."""
    prod_store([STOJKA_GROUP, PALOCHKI_GROUP], PALOCHKI_PRODUCTS)
    manifest = _run()
    text = _batch_text(manifest)
    assert "Стойка на 13 шаров" in text
    assert "палочки" not in text
    assert manifest["sku_matched_pages"] == 1


def test_name_guess_does_not_override_known_composition(prod_store):
    """
    Состав известен, ни один артикул не в группе — штампа нет.

    Прежде здесь печаталась группа, найденная по названию первого товара, то
    есть чужая: раз артикулы на руках, «похожее название» означает лишь
    соседнюю карточку каталога.
    """
    prod_store([PALOCHKI_GROUP], PALOCHKI_PRODUCTS)
    manifest = _run()
    assert manifest["grouped_pages"] == 0
    assert manifest["ungrouped_pages"] == 1
    assert "палочки" not in _batch_text(manifest)


def test_name_guess_still_works_without_composition(prod_store):
    """
    Без состава заказа (окно Order API — 13 дней) прежний путь остаётся, но
    штамп помечен как догадка: «?» перед названием и счётчик в манифесте,
    по которому кабинет показывает предупреждение.
    """
    prod_store([PALOCHKI_GROUP], PALOCHKI_PRODUCTS)
    manifest = _run(order_skus=None)
    assert manifest["grouped_pages"] == 1
    assert manifest["guessed_group_pages"] == 1
    assert manifest["no_composition_pages"] == 1
    text = _batch_text(manifest)
    assert "палочки" in text
    # Какой бокс встанет — компактный под названием магазина или большой с
    # заголовком — зависит от накладной; догадка помечена в обоих.
    assert "? H422-0002" in text or "ГРУППА? (по названию)" in text


def test_sku_stamp_is_not_marked_as_guess(prod_store):
    prod_store([STOJKA_GROUP, PALOCHKI_GROUP], PALOCHKI_PRODUCTS)
    manifest = _run()
    assert manifest["guessed_group_pages"] == 0
    assert manifest["no_composition_pages"] == 0
    assert "?" not in _batch_text(manifest)


def test_order_missing_from_composition_map_is_counted(prod_store):
    """Kaspi отдал состав, но не этого заказа (перепечатка ушедшего заказа)."""
    prod_store([PALOCHKI_GROUP], PALOCHKI_PRODUCTS)
    manifest = _run(order_skus={"999": ["X1"]})
    assert manifest["no_composition_pages"] == 1
    assert manifest["guessed_group_pages"] == 1


def test_mixed_order_gets_no_group_stamp(prod_store):
    """Товары заказа из разных групп — штамп описал бы лишь часть посылки."""
    other = {
        "group_id": "g-shary",
        "group_name": "H477-0006 Шары золотые звёзды",
        "linked_skus": ["H477-0006"],
        "is_active": True,
    }
    prod_store([STOJKA_GROUP, other])
    manifest = _run()
    assert manifest["mixed_group_pages"] == 1
    assert manifest["grouped_pages"] == 0
    text = _batch_text(manifest)
    assert "Стойка на 13 шаров" not in text and "золотые" not in text.lower()


def test_tags_of_every_item_are_printed(prod_store, monkeypatch):
    """Метка второго товара сборного заказа больше не теряется."""
    prod_store([STOJKA_GROUP])
    manifest = _run(product_tags={"H422-0003-1": ["Хрупкое"], "H477-0006": ["Проверить срок"]})
    text = _batch_text(manifest)
    assert "Хрупкое" in text and "Проверить" in text
    assert manifest["tagged_pages"] == 1
