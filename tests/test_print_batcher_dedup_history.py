"""
Регрессия на «Исключено дубликатов: 0» — суточный журнал печати не отсеивал
уже напечатанное.

Прод Бархана 2026-08-21: два архива накладных (413 и 480 файлов) прогнали
шесть раз подряд, в том числе один и тот же архив трижды, и КАЖДЫЙ прогон
рапортовал полное число новых листов при нуле дубликатов. Причин было две, и
обе воспроизводятся тестами ниже:

1. Один флаг `deduplicate_history` отвечал сразу за две разные вещи — «отсеять
   повторы в этом прогоне» и «вести журнал напечатанного». Выбор «печатать
   всё» в боте гасил вместе с фильтром и запись журнала, поэтому такая пачка
   не оставляла следов и следующая пачка честно считала её заказы новыми.
2. Журнал читался в начале задачи, а писался в конце — между этими точками
   десятки секунд разбора PDF. Две пачки, отправленные подряд, стартовали обе
   до того, как первая сохранит историю.
"""
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor

import fitz
import pytest

from backend.services import print_batcher_service as pb


def _make_waybill(order_id: int) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    page.insert_text((20, 40), f"Заказ 1 034 {order_id:06d}-1", fontsize=11)
    page.insert_text((20, 70), "1. Тестовый товар .......... 1 шт.", fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


def _archive(order_ids) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in order_ids:
            z.writestr(f"KASPI_SHOP-{1030000000 + i}.pdf", _make_waybill(i))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def clean_history(tmp_path, monkeypatch):
    """Журнал store_id=None — файл в системном temp; уводим его в tmp_path,
    чтобы тесты не цеплялись за реальную историю машины и друг за друга."""
    monkeypatch.setattr(pb, "HISTORY_FILE_PATH", tmp_path / "printed_history.json")
    yield


def _run(archive: bytes, **kwargs) -> dict:
    params = dict(
        file_bytes=archive,
        original_filename="waybill.zip",
        sort_preset="filename",
        auto_rotate=True,
        express_mode="ignore",
        output_layout="just_merge",
        store_id=None,
    )
    params.update(kwargs)
    return pb.process_zip_or_pdfs(**params)


def test_second_run_of_same_archive_is_fully_deduplicated():
    archive = _archive(range(5))

    first = _run(archive)
    second = _run(archive)

    assert first["new_pages"] == 5
    assert first["deduplicated_pages"] == 0
    assert second["new_pages"] == 0
    assert second["deduplicated_pages"] == 5
    assert second["all_duplicates"] is True


def test_print_all_mode_still_records_history():
    """Главный баг: пачка, напечатанная в режиме «все», обязана попасть в
    журнал — иначе следующая пачка не отличит в ней новое от повторов."""
    archive = _archive(range(5))

    first = _run(archive, filter_duplicates=False)
    assert first["new_pages"] == 5
    assert first["filter_duplicates"] is False

    second = _run(_archive(range(8)), filter_duplicates=True)
    assert second["deduplicated_pages"] == 5, "первые 5 уже печатались в режиме «все»"
    assert second["new_pages"] == 3


def test_force_reprint_prints_everything_but_keeps_history():
    archive = _archive(range(4))
    _run(archive)

    forced = _run(archive, force_reprint=True)
    assert forced["new_pages"] == 4
    assert forced["deduplicated_pages"] == 0

    # История не потеряна: обычный прогон снова видит их напечатанными.
    after = _run(archive)
    assert after["deduplicated_pages"] == 4


def test_history_can_be_switched_off_entirely():
    archive = _archive(range(4))
    _run(archive, deduplicate_history=False)
    assert pb.load_printed_history() == set()


def test_concurrent_batches_do_not_both_see_empty_history():
    """Две пачки, отправленные подряд: вторая обязана дождаться первой и
    увидеть её ключи, а не посчитать всё новым."""
    overlapping = _archive(range(6))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_run, overlapping) for _ in range(2)]
        results = [f.result() for f in futures]

    new_counts = sorted(r["new_pages"] for r in results)
    assert new_counts == [0, 6], f"обе пачки посчитали листы новыми: {new_counts}"


def test_excluded_express_pages_are_reported_not_silently_dropped():
    """Экспресс в режиме «Исключать» раньше растворялся в разнице между
    «Файлов в архиве» и «Всего листов» без единой строчки объяснения."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(3):
            z.writestr(f"KASPI_SHOP-{1030000000 + i}.pdf", _make_waybill(i))
        z.writestr("KASPI_EXPRESS-1030009999.pdf", _make_waybill(9999))

    res = _run(buf.getvalue(), express_mode="exclude")

    assert res["excluded_express_pages"] == 1
    assert res["new_pages"] == 3
    assert res["total_pages"] == 4, "отброшенный лист всё равно был в архиве"
    assert res["all_duplicates"] is False


def test_peek_finds_already_printed_without_parsing_pdfs():
    """Предпроверка «есть ли повторы» читает только оглавление zip."""
    archive = _archive(range(5))
    assert pb.peek_already_printed(archive, "waybill.zip") == (0, 5)

    _run(archive)
    already, total = pb.peek_already_printed(archive, "waybill.zip")
    assert (already, total) == (5, 5)

    mixed = _archive(range(3, 9))
    already, total = pb.peek_already_printed(mixed, "waybill.zip")
    assert (already, total) == (2, 6), "пересечение 3..4 из уже напечатанных 0..4"


def test_peek_degrades_quietly_on_unusable_input():
    """Одиночный PDF и мусор не должны ронять поток — вызывающий просто спросит."""
    assert pb.peek_already_printed(b"whatever", "single.pdf") == (0, 0)
    assert pb.peek_already_printed(b"not a zip at all", "waybill.zip") == (0, 0)


def test_count_already_printed_orders_for_spool():
    _run(_archive(range(4)))
    codes = [str(1030000000 + i) for i in range(6)]
    assert pb.count_already_printed_orders(codes) == 4
    assert pb.count_already_printed_orders([]) == 0


def test_reset_history_makes_everything_new_again():
    archive = _archive(range(5))
    _run(archive)
    assert _run(archive)["deduplicated_pages"] == 5

    pb.reset_printed_history()

    assert _run(archive)["new_pages"] == 5
