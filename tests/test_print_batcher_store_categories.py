"""
Крупные категории магазина («огромные категории» поверх Мастер-Групп).

У Бархана артикул продавца устроен как <БУКВА><3 цифры>-<4 цифры>, и первая
буква — крупный раздел склада. Упаковщику нужна сначала вся стопка одного
раздела, внутри неё — блоки Мастер-Групп, а между разделами — разделительный
лист с названием следующего. Пресет задаётся ПОМАГАЗИННО и другим магазинам
ничего не навязывает.
"""
import io
import zipfile

import fitz
import pytest

from backend.services import print_batcher_service as pb


# Встроенные base14-шрифты PyMuPDF кириллицу не содержат: без внешнего TTF
# «шт.» превращается в «··.», шаблон разбора товара не срабатывает и тест
# проверял бы пустоту вместо категорий. Берём тот же шрифт, что и штампы.
_CYR_FONT = pb._resolve_stamp_fonts()[1]
pytestmark = pytest.mark.skipif(
    not _CYR_FONT, reason="в системе нет кириллического TTF — накладную не подделать"
)


def _waybill(order_id: int, product_line: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    page.insert_text((20, 40), f"1 04{order_id:07d} 1", fontsize=11,
                     fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((20, 70), f"{product_line} . . . . . . . . . .  1 шт.", fontsize=7,
                     fontname="cyr", fontfile=_CYR_FONT)
    data = doc.tobytes()
    doc.close()
    return data


def _archive(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, line in enumerate(entries):
            z.writestr(f"KASPI_SHOP-104{i:07d}.pdf", _waybill(i, line))
    return buf.getvalue()


RULES = {
    "enabled": True,
    "separator_pages": True,
    "uncategorized_title": "Прочее",
    "categories": [
        {"prefix": "S", "title": "Спорт"},
        {"prefix": "D", "title": "Дом"},
    ],
}


@pytest.fixture(autouse=True)
def clean_history(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "HISTORY_FILE_PATH", tmp_path / "printed_history.json")


def _run(archive, **kwargs):
    params = dict(
        file_bytes=archive,
        original_filename="waybill.zip",
        sort_preset="product_name",
        auto_rotate=True,
        express_mode="ignore",
        output_layout="thermal_75_120",
        store_id=None,
        deduplicate_history=False,
        filter_duplicates=False,
    )
    params.update(kwargs)
    return pb.process_zip_or_pdfs(**params)


# --- определение префикса -------------------------------------------------

def test_prefix_is_found_anywhere_in_the_string():
    """У части групп Бархана артикул стоит внутри названия, а не в начале."""
    assert pb.extract_article_prefix("S517-0027 черный") == "S"
    assert pb.extract_article_prefix("Кольцо для салфеток K474-0003, металл") == "K"
    assert pb.extract_article_prefix("Barkhan D618-0011 МДФ") == "D"
    assert pb.extract_article_prefix("Товар без артикула") is None


def test_prefix_falls_through_candidates_in_order():
    assert pb.extract_article_prefix(None, "", "X179-0014 Органайзер") == "X"


def test_detect_prefixes_counts_and_sorts_by_volume():
    products = (
        [{"merchantSku": f"D618-{i:04d}", "name": "x"} for i in range(5)]
        + [{"merchantSku": f"S517-{i:04d}", "name": "x"} for i in range(2)]
        + [{"merchantSku": "без-артикула", "name": "x"}]
    )
    found = pb.detect_article_prefixes(products)
    assert [(e["prefix"], e["products"]) for e in found] == [("D", 5), ("S", 2)]
    assert found[0]["examples"][0].startswith("D618-")


# --- резолвер -------------------------------------------------------------

def test_disabled_resolver_puts_everything_in_one_bucket():
    r = pb.StoreCategoryResolver({"enabled": False, "categories": [{"prefix": "S", "title": "Спорт"}]})
    key, title, order = r.resolve("S517-0027")
    assert key == pb.UNCATEGORIZED_KEY


def test_unnamed_prefix_is_surfaced_not_hidden():
    """Неназванный префикс обязан быть виден владельцу, а не слиться с «Прочим»."""
    r = pb.StoreCategoryResolver(RULES)
    assert r.resolve("S517-0027") == ("S", "Спорт", 0)
    key, title, order = r.resolve("H422-0004")
    assert key == "H"
    assert "H" in title and title != "Прочее"
    assert order > 1, "неописанные категории печатаются последними"


def test_category_order_follows_the_configured_list():
    r = pb.StoreCategoryResolver(RULES)
    assert r.resolve("S517-0001")[2] < r.resolve("D618-0001")[2]


def test_articles_without_prefix_go_to_uncategorized():
    r = pb.StoreCategoryResolver(RULES)
    key, title, _ = r.resolve("совсем без артикула")
    assert key == pb.UNCATEGORIZED_KEY
    assert title == "Прочее"


# --- сквозной прогон ------------------------------------------------------

def test_pages_are_ordered_by_category_then_grouped():
    archive = _archive([
        "Дом Barkhan D618-0011 МДФ",
        "Насос Barkhan S517-0003 33см",
        "Ваза Barkhan D675-0016 стекло",
        "Фонарь Barkhan S517-0027 черный",
    ])
    res = _run(archive, store_categories=RULES)
    titles = [c["title"] for c in res["store_categories"]]
    assert titles == ["Спорт", "Дом"], "порядок категорий задаётся пресетом, а не архивом"
    assert [c["pages"] for c in res["store_categories"]] == [2, 2]


def test_separator_page_is_added_before_each_category():
    archive = _archive([
        "Насос Barkhan S517-0003 33см",
        "Ваза Barkhan D675-0016 стекло",
    ])
    with_sep = _run(archive, store_categories=RULES)
    without_sep = _run(archive, store_categories={**RULES, "separator_pages": False})

    batch = with_sep["batches"]["THERMAL_75x120"]
    plain = without_sep["batches"]["THERMAL_75x120"]
    assert batch["total_pages"] == plain["total_pages"] + 2, "по разделителю на категорию"
    assert with_sep["separator_pages"] is True
    assert without_sep["separator_pages"] is False


def test_separator_keeps_the_batch_geometry():
    """Лист чужого размера застрянет в термопринтере — разделитель обязан быть 75x120."""
    archive = _archive(["Насос Barkhan S517-0003", "Ваза Barkhan D675-0016"])
    res = _run(archive, store_categories=RULES)
    path = pb.get_batch_file_path(res["batch_id"], "THERMAL_75x120")
    doc = fitz.open(str(path))
    try:
        sizes = {(round(p.rect.width), round(p.rect.height)) for p in doc}
    finally:
        doc.close()
    assert len(sizes) == 1, f"разъехалась геометрия страниц: {sizes}"


def test_store_without_preset_is_untouched():
    """Пресет одного магазина не должен влиять на остальных."""
    archive = _archive(["Насос Barkhan S517-0003", "Ваза Barkhan D675-0016"])
    res = _run(archive, store_categories=None)
    assert res["store_categories"] == []
    assert res["separator_pages"] is False


def test_categories_ignored_when_sorting_knows_nothing_about_products():
    """Сортировка по номеру заказа не извлекает товар — категорию взять неоткуда."""
    archive = _archive(["Насос Barkhan S517-0003", "Ваза Barkhan D675-0016"])
    res = _run(archive, sort_preset="order_id", store_categories=RULES)
    assert res["store_categories"] == []


@pytest.mark.parametrize("layout,batch_key", [
    ("thermal_75_120", "THERMAL_75x120"),
    ("thermal_100_150", "THERMAL_100x150"),
    ("a4_4", "LAYOUT_A4_4"),
    ("a4_1", "LAYOUT_A4_1"),
    ("just_merge", "MERGED_ALL"),
])
def test_separators_work_in_every_layout(layout, batch_key):
    """Разделитель обещан для всех раскладок — значит все и проверяем."""
    archive = _archive([
        "Насос Barkhan S517-0003 33см",
        "Фонарь Barkhan S517-0027 черный",
        "Ваза Barkhan D675-0016 стекло",
    ])
    with_sep = _run(archive, output_layout=layout, store_categories=RULES)
    without = _run(archive, output_layout=layout,
                   store_categories={**RULES, "separator_pages": False})

    assert with_sep["batches"][batch_key]["total_pages"] > without["batches"][batch_key]["total_pages"], (
        f"в раскладке {layout} разделители не появились"
    )
    assert [c["title"] for c in with_sep["store_categories"]] == ["Спорт", "Дом"]


def test_auto_layout_keeps_categories_and_separators():
    archive = _archive([
        "Насос Barkhan S517-0003 33см",
        "Ваза Barkhan D675-0016 стекло",
    ])
    res = _run(archive, output_layout="auto", store_categories=RULES)
    total = sum(b["total_pages"] for b in res["batches"].values())
    plain = _run(archive, output_layout="auto",
                 store_categories={**RULES, "separator_pages": False})
    assert total == sum(b["total_pages"] for b in plain["batches"].values()) + 2
