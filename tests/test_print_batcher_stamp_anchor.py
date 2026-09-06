"""
Штамп группы/меток вешается под названием магазина на накладной, а не в
первое найденное свободное место.

Название магазина — поле шаблона Kaspi (короткая строка ЗАГЛАВНЫМИ на
фиксированном месте), а не наша догадка: проверено на 236 из 236 обычных
накладных живой партии Бархана (2026-09-04) — все на одних и тех же
координатах, с одним и тем же зазором 45pt до следующего блока. Раньше
штамп искал первую свободную полосу по всей странице — предсказать, где
он окажется на конкретной накладной, было нельзя.
"""
import fitz
import pytest

from backend.services import print_batcher_service as pb

_CYR_FONT = pb._resolve_stamp_fonts()[1]
pytestmark = pytest.mark.skipif(
    not _CYR_FONT, reason="в системе нет кириллического TTF — накладную не подделать"
)


def _waybill_with_store_name(store_name: str = "BARKHAN", with_barcode: bool = False) -> fitz.Document:
    """
    Накладная с полем названия магазина в том же месте, что и на реальных
    накладных Kaspi (см. temp/-замеры в докстринге draw_group_stamp):
    короткая строка заглавными в верхней трети листа, дальше — адрес.

    with_barcode=True — картинка справа от названия магазина, на реальном
    расстоянии от левого края (x0=175.4pt, замер на 236 накладных живой
    партии). Компактный бокс ищет свободное место КАК ИЗОБРАЖЕНИЕ, а не
    текст — синтетика без него однажды пропустила реальную коллизию
    (2026-09-07): бокс фиксированной ширины упирался в штрихкод на каждой
    из 236 накладных и штамп молча улетал на старый обход всей страницы.
    """
    doc = fitz.open()
    page = doc.new_page(width=595.275, height=841.875)
    page.insert_text((90, 65), "1061448162", fontsize=11, fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((22, 140), "Товар для теста . . . . . . . . . . .  1 шт.", fontsize=7,
                     fontname="cyr", fontfile=_CYR_FONT)
    page.insert_text((11, 192), store_name, fontsize=10, fontname="cyr", fontfile=_CYR_FONT)
    if with_barcode:
        pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 2, 2), False)
        pix.clear_with(0)
        page.insert_image(fitz.Rect(175.4, 210.0, 285.6, 239.0), pixmap=pix)
    page.insert_text((11, 250), "Алматы, мкр. Кулагер, д. 10", fontsize=8,
                     fontname="cyr", fontfile=_CYR_FONT)
    return doc


def test_stamp_lands_right_under_store_name():
    doc = _waybill_with_store_name()
    page = doc[0]
    pb.draw_group_stamp(page, page.rect, page, page.rect, "Органайзеры", ["Хрупкое"])
    doc.close()

    doc = _waybill_with_store_name()
    page = doc[0]
    anchor = pb._find_store_name_anchor(page, page.rect)
    assert anchor is not None
    pb.draw_group_stamp(page, page.rect, page, page.rect, "Органайзеры", ["Хрупкое"])

    drawings = [d["rect"] for d in page.get_drawings() if d.get("type") == "s"]
    assert drawings, "штамп должен нарисовать прямоугольную рамку"
    box = drawings[0]
    assert box.y0 > anchor.y1, "штамп должен быть НИЖЕ названия магазина"
    assert box.y0 - anchor.y1 < 15, "штамп должен быть прямо под названием, а не где-то ниже"
    doc.close()


def test_compact_stamp_has_no_header_text():
    doc = _waybill_with_store_name()
    page = doc[0]
    pb.draw_group_stamp(page, page.rect, page, page.rect, "Органайзеры", ["Хрупкое"])
    text = pb.normalize_waybill_text(page.get_text())
    doc.close()
    assert "Органайзеры" in text
    assert "Хрупкое" in text
    assert "ГРУППА" not in text
    assert "МЕТКИ" not in text


def test_compact_stamp_avoids_barcode_next_to_store_name():
    """
    Регрессия: фиксированная ширина бокса (32% страницы) упиралась в
    штрихкод справа от названия магазина на КАЖДОЙ из 236 реальных
    накладных живой партии и штамп молча падал на старый обход — с
    заголовком и совсем в другом месте страницы. Ширина бокса должна
    подстраиваться под то, что реально свободно, а не собьётся с якоря при
    первом же соседнем изображении.
    """
    doc = _waybill_with_store_name(with_barcode=True)
    page = doc[0]
    anchor = pb._find_store_name_anchor(page, page.rect)
    assert anchor is not None

    pb.draw_group_stamp(page, page.rect, page, page.rect, "Органайзеры", ["Хрупкое"])
    text = pb.normalize_waybill_text(page.get_text())
    drawings = [d["rect"] for d in page.get_drawings() if d.get("type") == "s"]
    doc.close()

    assert "ГРУППА" not in text, "штамп не должен был свалиться на старый обход со штрихкодом рядом"
    assert "Органайзеры" in text and "Хрупкое" in text
    assert drawings, "штамп должен нарисовать рамку"
    box = drawings[0]
    assert box.x1 <= 175.4, "бокс должен остановиться до штрихкода, а не наехать на него"
    assert box.y0 - anchor.y1 < 15, "штамп должен остаться прямо под названием магазина"


def test_falls_back_to_free_space_search_without_store_name():
    """Нет поля с названием магазина (другой шаблон) — прежнее поведение с заголовком."""
    doc = fitz.open()
    page = doc.new_page(width=290, height=417)
    assert pb._find_store_name_anchor(page, page.rect) is None
    pb.draw_group_stamp(page, page.rect, page, page.rect, "Органайзеры", ["Хрупкое"])
    text = pb.normalize_waybill_text(page.get_text())
    doc.close()
    assert "ГРУППА" in text
    assert "Органайзеры" in text


def test_express_sample_header_does_not_trigger_anchor():
    """
    "ОБРАЗЕЦ ПАРТНЕРА" — заголовок экспресс-листа Kaspi, попадает под ту же
    регулярку (короткая строка заглавными), что и название магазина, но
    сидит у самого верха страницы. top_guard в _find_store_name_anchor не
    должен принять его за название магазина.
    """
    doc = fitz.open()
    page = doc.new_page(width=1860, height=2631)
    page.insert_text((21, 25), "ОБРАЗЕЦ ПАРТНЕРА", fontsize=20, fontname="cyr", fontfile=_CYR_FONT)
    anchor = pb._find_store_name_anchor(page, page.rect)
    doc.close()
    assert anchor is None
