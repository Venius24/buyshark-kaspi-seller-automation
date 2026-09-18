"""
Метки товара на этикетке.

Мастер-Группа существует ради общего остатка, и печать лишь пользовалась ею
как способом сгруппировать накладные — товар без группы не получал на
этикетку ничего. Метка вешается на артикул, поэтому работает в обоих случаях
и печатается в том же боксе, что и штамп группы.
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


def _waybill(order_code: str, product_line: str) -> bytes:
    """
    Накладная во всю высоту листа, как настоящая: размер штампа считается от
    высоты СОДЕРЖИМОГО (get_content_rect), и на накладной из двух строчек в
    углу бокс выходит высотой 5 pt — туда не влезает никакой текст, включая
    название группы. Свободная полоса под штамп остаётся в середине.
    """
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    page.insert_text((20, 40), order_code, fontsize=11, fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((20, 70), f"{product_line} . . . . . . . . . .  1 шт.", fontsize=7,
                     fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((20, 300), "Астана", fontsize=9, fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((20, 390), "план дата доставки:", fontsize=7, fontname="cyr", fontfile=_CYR_FONT)
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
    )
    params.update(kwargs)
    return pb.process_zip_or_pdfs(**params)


def _batch_text(manifest):
    path = pb.get_batch_file_path(manifest["batch_id"], "THERMAL_75x120")
    doc = fitz.open(str(path))
    text = "\n".join(p.get_text() for p in doc)
    doc.close()
    # PyMuPDF отдаёт вставленный текст с неразрывными пробелами и мягкими
    # дефисами — сравнивать с ними напрямую бессмысленно.
    return pb.normalize_waybill_text(text)


ARCHIVE_ENTRIES = [("1061556422", "Флакон дорожный наливным путем 100 мл")]


def test_tag_is_printed_for_product_without_master_group():
    manifest = _run(
        _archive(ARCHIVE_ENTRIES),
        order_skus={"1061556422": ["X179-0013"]},
        product_tags={"X179-0013": ["Хрупкое"]},
    )
    text = _batch_text(manifest)
    assert "МЕТКИ" in text
    assert "Хрупкое" in text
    assert manifest["tagged_pages"] == 1


def test_several_tags_are_joined_in_one_box():
    manifest = _run(
        _archive(ARCHIVE_ENTRIES),
        order_skus={"1061556422": ["X179-0013"]},
        product_tags={"X179-0013": ["Хрупкое", "Проверить"]},
    )
    text = _batch_text(manifest)
    assert "Хрупкое" in text and "Проверить" in text


def test_no_tags_no_box():
    manifest = _run(
        _archive(ARCHIVE_ENTRIES),
        order_skus={"1061556422": ["X179-0013"]},
        product_tags={"X999-0001": ["Хрупкое"]},
    )
    assert manifest["tagged_pages"] == 0
    assert "МЕТКИ" not in _batch_text(manifest)


def test_tags_do_not_replace_group_stamp():
    """Метка печатается рядом с группой, а не вместо неё."""
    groups = [{
        "group_id": "g1",
        "group_name": "X179-0013 Флакон дорожный",
        "linked_skus": ["X179-0013"],
        "is_active": True,
    }]
    item = pb.PageItem(
        pdf_path="x", page_num=0, source_filename="x.pdf", original_order=0,
        w_mm=75, h_mm=120, category="STICKER_75x120", order_id="1", rotation=0,
        is_express=False, unique_key="k", stamp_label=groups[0]["group_name"],
        tags=["Хрупкое"],
    )
    assert item.stamp_label and item.tags

    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    pb.draw_group_stamp(page, page.rect, page, page.rect, item.stamp_label, item.tags)
    text = pb.normalize_waybill_text(page.get_text())
    doc.close()
    assert "ГРУППА" in text
    assert "X179-0013" in text
    assert "Хрупкое" in text


def test_guessed_stamp_has_question_header():
    """Большой бокс (без якоря под названием магазина): заголовок говорит, что это догадка."""
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    pb.draw_group_stamp(page, page.rect, page, page.rect, "X179-0013 Флакон", [], guessed=True)
    text = pb.normalize_waybill_text(page.get_text())
    doc.close()
    assert "ГРУППА?" in text and "по названию" in text


def test_stamp_is_skipped_when_nothing_to_show():
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    pb.draw_group_stamp(page, page.rect, page, page.rect, None, [])
    text = page.get_text()
    doc.close()
    assert text.strip() == ""
