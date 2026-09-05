"""
Извлечение названия товара из текстового слоя накладной Kaspi.

Название — единственный вход и для Мастер-Группы (штамп), и для крупной
категории (префикс артикула). Все случаи ниже взяты из живой партии Бархана
2026-09-04: там 33 накладные уехали в «Прочее», и 19 из них — из-за того, что
название извлекалось обрывком переноса или не извлекалось вовсе, хотя артикул
физически напечатан на этикетке.
"""
from backend.services.print_batcher_service import (
    extract_article_prefix,
    extract_product_name,
)


def test_dot_leader_basic():
    text = "1.  \n1/1\nBarkhan S517-0027 черный . . . . . . . . . .  1 шт.\nBARKHAN\n"
    assert extract_product_name(text, "f.pdf") == "Barkhan S517-0027 черный"


def test_wrapped_name_is_joined_not_truncated_to_tail():
    """Перенос названия целиком: раньше возвращался хвост 'на 10 пе'."""
    text = (
        "1.  \n1 061 667 176 1\n-\n"
        "Barkhan H974-0004 Праздничная одноразовая посуда для праздника \n"
        "на 10 пе . . . . . . . . . . . . . . . . . .  1 шт.\n"
        "P2\nАстана\n"
    )
    name = extract_product_name(text, "f.pdf")
    assert name.startswith("Barkhan H974-0004 Праздничная одноразовая посуда")
    assert extract_article_prefix(name) == "H"


def test_single_dot_leader_is_enough():
    """Длинное название оставляет всего одну точку пунктира — не повод терять товар."""
    text = (
        "1.  \n1 061 711 226 1\n-\n"
        'H477-0024 Набор воздушных шаров Barkhan "Черно-красный" 50 шт .  1 \n'
        "шт.\nT8\n"
    )
    name = extract_product_name(text, "f.pdf")
    assert name.startswith("H477-0024 Набор воздушных шаров")
    assert extract_article_prefix(name) == "H"


def test_no_dot_leader_at_all():
    """Название заняло всю ширину — пунктира нет вовсе, счётчик уехал на строку ниже."""
    text = (
        "1.  \n1 061 801 870 1\n-\n"
        'H477-0030 Набор воздушных шаров Barkhan "Большой футбол" 116 шт  \n'
        "1 шт.\n102\nАстана\n"
    )
    name = extract_product_name(text, "f.pdf")
    assert name.startswith("H477-0030 Набор воздушных шаров")
    assert extract_article_prefix(name) == "H"


def test_second_item_on_multi_item_label_is_not_glued_to_first():
    """Две позиции подряд: закрытая строка не должна прилипать к предыдущей закрытой."""
    text = (
        "1.  \n2.  \n1 062 081 552 1\n-\n"
        "Настенный держатель бумажных полотенец X171-0027 из \n"
        "нержавеющей стали  . . . . . . . . . . . .  1 шт.\n"
        "Диспенсер для туалетной бумаги Barkhan X171-0026 . . . . . . .  1 шт.\n"
    )
    name = extract_product_name(text, "f.pdf")
    assert name.startswith("Настенный держатель бумажных полотенец X171-0027")
    assert "Диспенсер" not in name


def test_courier_receipt_template_wins_over_loose_pass():
    """
    У квитанции «НА ТОВАР» свой шаблон: ослабленный проход выдёргивал из неё
    обрывки вроде '"Happy Birthday" 53 шт' и перехватывал управление.
    """
    text = (
        "НА ТОВАР\n"
        "Клиент (фио/подпись):____\n"
        'Набор воздушных шаров Barkhan "Happy Birthday" 53 шт\n'
        "1 061 603 347\n"
    )
    assert extract_product_name(text, "f.pdf") == (
        'Набор воздушных шаров Barkhan "Happy Birthday" 53 шт'
    )


def test_service_digits_never_become_a_name():
    """Служебные строки накладной не должны пролезать в название."""
    text = "1.  \n1/1\n1 061 466 268 1\n-\n1 шт.\nBARKHAN\n"
    assert extract_product_name(text, "f.pdf") is None
