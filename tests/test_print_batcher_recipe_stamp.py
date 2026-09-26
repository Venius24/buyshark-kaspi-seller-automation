"""
Штамп на накладной по рецепту списания (sku_recipes, §3.103).

Упаковщик видит на накладной название группы — то есть полку, откуда брать.
Для двойной упаковки этого мало (возьмёт одну), для набора — тоже (возьмёт
съёмник и забудет ключ). Поэтому штамп дописывает ×N и состав набора, но
только когда артикул заказа известен точно.
"""
import io
import zipfile

import fitz
import pytest

from backend.services import print_batcher_service as pb


def _group(gid, name, skus, recipes=None, active=True):
    return {"group_id": gid, "group_name": name, "linked_skus": skus,
            "is_active": active, "sku_recipes": recipes or {}}


G46 = _group("g46", "S517-0046 Съёмник", ["S517-0046", "S517-0046-48"],
             {"S517-0046-48": {"qty": 1, "extra": [{"group_id": "g48", "qty": 1}]}})
G48 = _group("g48", "S517-0048 Ключ", ["S517-0048"])
H422 = _group("h422", "H422-0007", ["H422-0007-9", "H422-0007-9 2шт"],
              {"H422-0007-9 2шт": {"qty": 2}})
PLAIN_NAMES = _group("gx", "Подставка деревянная", ["X1-0001", "X1-0002"],
                     {"X1-0002": {"qty": 1, "extra": [{"group_id": "gy", "qty": 3}]}})
GY = _group("gy", "Крючки для подставки", ["Y1-0001"], active=False)

GROUPS = [G46, G48, H422, PLAIN_NAMES, GY]


@pytest.fixture
def matcher():
    return pb.MasterGroupMatcher(GROUPS, [])


def test_plain_sku_keeps_group_name(matcher):
    assert matcher.stamp_label(H422, ["H422-0007-9"]) == "H422-0007"
    assert matcher.stamp_label(G46, ["S517-0046"]) == "S517-0046 Съёмник"


def test_pack_gets_multiplier(matcher):
    assert matcher.stamp_label(H422, ["H422-0007-9 2шт"]) == "H422-0007 ×2"


def test_bundle_lists_components_by_code(matcher):
    """«48», а не «S517-0048»: так владелец сам пишет артикул набора."""
    assert matcher.stamp_label(G46, ["S517-0046-48"]) == "НАБОР: S517-0046 + 48"


def test_bundle_without_codes_uses_names_and_counts_inactive_part(matcher):
    """Выключенная группа остатком не управляется, но в коробку её товар кладут."""
    assert (matcher.stamp_label(PLAIN_NAMES, ["X1-0002"])
            == "НАБОР: Подставка деревянная + Крючки для подставки ×3")


def test_mixed_recipes_in_one_order_fall_back_to_name(matcher):
    """×1 и ×2 одной группы в одном заказе — один штамп на двоих соврал бы."""
    assert matcher.stamp_label(H422, ["H422-0007-9", "H422-0007-9 2шт"]) == "H422-0007"


def test_same_recipe_twice_is_fine(matcher):
    assert matcher.stamp_label(H422, ["H422-0007-9 2шт", "h422-0007-9  2ШТ"]) == "H422-0007 ×2"


def test_deleted_component_is_dropped(matcher):
    g = _group("g1", "S517-0046", ["S1"], {"S1": {"qty": 2, "extra": [{"group_id": "gone", "qty": 1}]}})
    m = pb.MasterGroupMatcher([g], [])
    assert m.stamp_label(g, ["S1"]) == "S517-0046 ×2"


@pytest.mark.parametrize("first,code,want", [
    ("S517-0046", "S517-0048", "48"),
    ("S517-0046", "S514-0048", "S514-0048"),
    ("S517-0046", "X1", "X1"),
    ("S517-0046", "S517-00AB", "00AB"),
    ("A-B-0010", "A-B-0000", "0"),
])
def test_shorten_code(first, code, want):
    assert pb._shorten_code(first, code) == want


def test_group_code():
    assert pb._group_code("S517-0048 Ключ") == "S517-0048"
    assert pb._group_code("s517-0048") == "S517-0048"
    assert pb._group_code("250мл флакон") == "250мл флакон"
    assert pb._group_code("Ключ") == "Ключ"


# --- сквозной прогон: штамп доезжает до накладной ---------------------------

_CYR_FONT = pb._resolve_stamp_fonts()[1]


def _waybill(order_code: str, product_line: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    page.insert_text((20, 40), order_code, fontsize=11, fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((20, 70), f"{product_line} . . . . . . . . . .  1 шт.", fontsize=7,
                     fontname="cyr", fontfile=_CYR_FONT)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.skipif(not _CYR_FONT, reason="в системе нет кириллического TTF — накладную не подделать")
def test_pipeline_prints_bundle_and_pack_stamps(tmp_path, monkeypatch):
    import backend.state_helpers as sh

    monkeypatch.setattr(pb, "HISTORY_FILE_PATH", tmp_path / "printed_history.json")
    monkeypatch.setattr(sh, "_load_master_stock_groups", lambda store_id: {g["group_id"]: g for g in GROUPS})
    monkeypatch.setattr(sh, "_load_products", lambda store_id: [])
    monkeypatch.setattr(sh, "_load_product_tags", lambda store_id: {})
    drawn = []
    monkeypatch.setattr(pb, "draw_group_stamp",
                        lambda *a, **kw: drawn.append(a[4]))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("KASPI_SHOP-1000000001.pdf", _waybill("1000000001", "Набор для ремонта Barkhan"))
        z.writestr("KASPI_SHOP-1000000002.pdf", _waybill("1000000002", "Подставка Barkhan 2 шт"))
        z.writestr("KASPI_SHOP-1000000003.pdf", _waybill("1000000003", "Подставка Barkhan"))

    pb.process_zip_or_pdfs(
        file_bytes=buf.getvalue(),
        original_filename="waybill.zip",
        sort_preset="master_group",
        auto_rotate=True,
        express_mode="ignore",
        output_layout="thermal_75_120",
        store_id="store-1",
        deduplicate_history=False,
        filter_duplicates=False,
        order_skus={
            "1000000001": ["S517-0046-48"],
            "1000000002": ["H422-0007-9 2шт"],
            "1000000003": ["H422-0007-9"],
        },
    )

    assert sorted(drawn) == sorted(["НАБОР: S517-0046 + 48", "H422-0007 ×2", "H422-0007"])
