"""
Регрессия на исчерпание файловых дескрипторов при склейке крупных партий.

Архив Kaspi — один PDF на заказ, поэтому партия в 1200 накладных открывала
1200 исходных документов. Хуже того, show_pdf_page/insert_pdf прививают
источник к результату, и MuPDF держит файловый хэндл источника даже после
.close(), пока жив итоговый документ. На проде это выглядело как
"Failed to open file ... as type pdf", а на деле было OSError 24
(Too many open files), который заодно ронял приём HTTP-соединений сервиса.
"""
import io
import zipfile

import fitz
import pytest

from backend.services import print_batcher_service as pb


# С запасом больше порога, на котором падала прежняя реализация (509 накладных).
LARGE_BATCH = 700


def _make_waybill(order_id: int) -> bytes:
    """Одностраничная накладная в геометрии термоэтикетки Kaspi."""
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    page.insert_text((20, 40), f"Заказ 1 034 {order_id:06d}-1", fontsize=11)
    page.insert_text((20, 70), "1. Тестовый товар .......... 1 шт.", fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def _make_archive(count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(count):
            z.writestr(f"KASPI_SHOP-{1030000000 + i}.pdf", _make_waybill(i))
    return buf.getvalue()


@pytest.fixture(scope="module")
def large_archive() -> bytes:
    return _make_archive(LARGE_BATCH)


@pytest.mark.parametrize("layout", ["thermal_75_120", "a4_4", "just_merge", "auto"])
def test_large_archive_does_not_exhaust_file_descriptors(large_archive, layout):
    res = pb.process_zip_or_pdfs(
        file_bytes=large_archive,
        original_filename="waybill.zip",
        sort_preset="filename",
        auto_rotate=True,
        express_mode="separate",
        deduplicate_history=False,
        force_reprint=True,
        output_layout=layout,
        store_id=None,
    )

    assert res["total_files"] == LARGE_BATCH
    assert res["total_pages"] == LARGE_BATCH
    assert res["skipped_files"] == 0
    assert res["batches"], "ни одного батча не сформировано"


def test_doc_cache_keeps_at_most_limit_documents(tmp_path):
    paths = []
    for i in range(pb.DOC_CACHE_LIMIT * 3):
        p = tmp_path / f"w{i}.pdf"
        p.write_bytes(_make_waybill(i))
        paths.append(str(p))

    cache = pb._DocCache()
    try:
        for p in paths:
            assert cache.get(p).page_count == 1
            assert len(cache._docs) <= pb.DOC_CACHE_LIMIT
    finally:
        cache.close_all()

    assert len(cache._docs) == 0


def test_doc_cache_reuses_open_document(tmp_path):
    p = tmp_path / "w.pdf"
    p.write_bytes(_make_waybill(1))

    cache = pb._DocCache()
    try:
        assert cache.get(str(p)) is cache.get(str(p))
    finally:
        cache.close_all()


def test_broken_pdf_does_not_kill_the_whole_batch():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("KASPI_SHOP-1030000001.pdf", _make_waybill(1))
        z.writestr("KASPI_SHOP-1030000002.pdf", b"not a pdf at all")
        z.writestr("KASPI_SHOP-1030000003.pdf", _make_waybill(3))

    res = pb.process_zip_or_pdfs(
        file_bytes=buf.getvalue(),
        original_filename="waybill.zip",
        sort_preset="filename",
        auto_rotate=True,
        express_mode="separate",
        deduplicate_history=False,
        force_reprint=True,
        output_layout="thermal_75_120",
        store_id=None,
    )

    assert res["skipped_files"] == 1
    assert res["total_pages"] == 2
    assert res["batches"]


def test_archive_of_only_broken_pdfs_reports_clear_error():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("KASPI_SHOP-1030000001.pdf", b"garbage")

    with pytest.raises(ValueError, match="не удалось прочитать"):
        pb.process_zip_or_pdfs(
            file_bytes=buf.getvalue(),
            original_filename="waybill.zip",
            sort_preset="filename",
            auto_rotate=True,
            express_mode="separate",
            deduplicate_history=False,
            force_reprint=True,
            output_layout="thermal_75_120",
            store_id=None,
        )
