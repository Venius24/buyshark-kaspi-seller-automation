"""
Сопоставление накладной по АРТИКУЛУ из состава заказа (Kaspi Order API).

На накладной Kaspi печатает название витрины, а не артикул продавца, поэтому
группа и категория раньше угадывались по тексту — со всеми вытекающими
(«Кисть кулинарная» получала штамп «K471-0005 масленка фигурная», партия
Бархана 2026-09-04). Код заказа на накладной есть всегда, а Order API по нему
отдаёт настоящие артикулы — здесь зафиксировано, что они главнее текста и что
без них всё продолжает работать по-старому.
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


def _group(gid, name, skus):
    return {"group_id": gid, "group_name": name, "linked_skus": skus, "is_active": True}


def _product(sku, name):
    return {"sku": sku, "merchantSku": sku, "kaspiSku": sku, "name": name}


# --- match_by_sku ---------------------------------------------------------

GROUPS = [
    _group("g1", "K471-0005 масленка фигурная 1 шт", ["K471-0005", "896893715"]),
    _group("g2", "X179-0013 Флакон дорожный наливным путем", ["X179-0013"]),
    _group("g3", "H477-0024 Набор воздушных шаров", ["H477-0024"]),
]
PRODUCTS = [
    _product("K471-0005", "Barkhan масленка фигурная 1 шт"),
    _product("X179-0013", "Флакон дорожный наливным путем пластик 100 мл 5 шт"),
]


@pytest.fixture
def matcher():
    return pb.MasterGroupMatcher(GROUPS, PRODUCTS)


def test_sku_beats_name_similarity(matcher):
    """
    Кисть кулинарная по названию похожа на набор с масленкой, но её артикул
    принадлежит другой группе — решает артикул.
    """
    assert matcher.match_by_sku("X179-0013")["group_id"] == "g2"


def test_sku_matches_group_absent_from_catalog(matcher):
    """
    Группы, чьих товаров нет в локальном каталоге, в индекс по названиям не
    попадают вовсе — по артикулу они всё равно находятся.
    """
    assert matcher.match("Набор воздушных шаров Barkhan") is None
    assert matcher.match_by_sku("H477-0024")["group_id"] == "g3"


def test_sku_is_case_and_space_insensitive(matcher):
    assert matcher.match_by_sku("  x179-0013 ")["group_id"] == "g2"


def test_unknown_sku_returns_none(matcher):
    assert matcher.match_by_sku("Z999-0001") is None
    assert matcher.match_by_sku(None) is None
    assert matcher.match_by_sku("") is None


def test_sku_in_two_groups_is_refused():
    """Артикул из нескольких групп — выбор был бы монеткой, лучше промолчать."""
    m = pb.MasterGroupMatcher(
        [_group("a", "Группа А", ["X1-0001"]), _group("b", "Группа Б", ["X1-0001"])],
        [],
    )
    assert m.match_by_sku("X1-0001") is None


# --- сквозной прогон конвейера -------------------------------------------

RULES = {
    "enabled": True,
    "separator_pages": False,
    "uncategorized_title": "Прочее",
    "categories": [{"prefix": "S", "title": "Спорт"}, {"prefix": "D", "title": "Дом"}],
}


def _waybill(order_code: str, product_line: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    page.insert_text((20, 40), order_code, fontsize=11, fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((20, 70), f"{product_line} . . . . . . . . . .  1 шт.", fontsize=7,
                     fontname="cyr", fontfile=_CYR_FONT)
    data = doc.tobytes()
    doc.close()
    return data


def _archive(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for code, line in entries:
            z.writestr(f"KASPI_SHOP-{code}.pdf", _waybill(code, line))
    return buf.getvalue()


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
        store_categories=RULES,
    )
    params.update(kwargs)
    return pb.process_zip_or_pdfs(**params)


def _titles(manifest):
    return {c["title"] for c in manifest["store_categories"]}


def test_category_comes_from_order_sku_when_name_has_no_article():
    """
    На накладной только название витрины без артикула — раньше это была
    гарантированная поездка в «Прочее».
    """
    archive = _archive([("1061556422", "Флакон дорожный наливным путем 100 мл")])

    assert _titles(_run(archive)) == {"Прочее"}
    assert _titles(_run(archive, order_skus={"1061556422": ["D970-0022"]})) == {"Дом"}


def test_order_sku_overrides_article_printed_in_name():
    """Артикул из заказа главнее того, что напечатано в названии."""
    archive = _archive([("1061556423", "Насос Barkhan S517-0003 33см")])

    assert _titles(_run(archive)) == {"Спорт"}
    assert _titles(_run(archive, order_skus={"1061556423": ["D970-0022"]})) == {"Дом"}


def test_missing_order_falls_back_to_name():
    """Заказа нет в карте (старая выгрузка вне окна API) — прежний путь."""
    archive = _archive([("1061556424", "Насос Barkhan S517-0003 33см")])
    manifest = _run(archive, order_skus={"9999999999": ["D970-0022"]})
    assert _titles(manifest) == {"Спорт"}
