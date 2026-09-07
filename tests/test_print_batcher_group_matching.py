"""
Сопоставление накладной с Мастер-Группой идёт по НАЗВАНИЯМ товаров.

В linked_skus группы лежат артикулы, а Kaspi печатает на накладной название
витрины — артикул в него попадает не всегда. Прежняя реализация искала
артикул подстрокой прямо в названии товара и на живой партии Бархана
(289 групп, 1141 накладная с распознанным названием) находила группу у 341
накладной. Здесь зафиксированы правила нового матчера, включая случаи,
где ошибка опаснее пропуска: соседние карточки Бархана различаются ТОЛЬКО
артикулом внутри названия.
"""
import pytest

from backend.services.print_batcher_service import MasterGroupMatcher


def _group(gid, name, skus, is_active=True):
    return {"group_id": gid, "group_name": name, "linked_skus": skus, "is_active": is_active}


def _product(sku, name, merchant_sku=None):
    return {"sku": sku, "merchantSku": merchant_sku or sku, "kaspiSku": sku, "name": name}


PRODUCTS = [
    _product("144444444", "Barkhan мыльница Дорожная 1 шт, пластик", "X147-0006"),
    _product("211111111", "Мыльница 855806678 1 шт, пластик", "X147-0002"),
    _product("399999999", "Выжимка для цепи Barkhan S517-0049 1", "S517-0049"),
    _product("400000000", "Фонарь Wellamart WL0043 красный", "S517-0054"),
    _product("533333333", "Салфетница Облачко, пластик, металл", "K474-0007"),
]

GROUPS = [
    _group("g-x147-0006", "X147-0006 мыльница Дорожная 1", ["144444444", "X147-0006"]),
    _group("g-x147-0002", "X147-0002 мыльница Urban White 1", ["211111111", "X147-0002"]),
    _group("g-s517-0049", "S517-0049 Выжимка для цепи 1", ["399999999", "S517-0049"]),
    _group("g-s517-0054", "S517-0054 черный", ["400000000", "S517-0054"]),
    _group("g-k474-0007", "K474-0007 салфетница пластик", ["533333333", "K474-0007"]),
]


@pytest.fixture
def matcher():
    return MasterGroupMatcher(GROUPS, PRODUCTS)


def test_exact_product_name_wins(matcher):
    g = matcher.match("Салфетница Облачко, пластик, металл")
    assert g["group_id"] == "g-k474-0007"


def test_name_matches_despite_extra_words(matcher):
    # Kaspi печатает "1 шт" и переставляет слова — название всё равно то же
    g = matcher.match("Фонарь Wellamart WL0043 красный 1 шт")
    assert g["group_id"] == "g-s517-0054"


def test_group_name_and_sku_alone_never_match():
    """
    Ключевая регрессия: название группы и её артикулы больше НЕ ищутся
    подстрокой в названии товара. Товар, не имеющий отношения к группе,
    но содержащий её слова, не должен получить чужой штамп.
    """
    groups = [_group("g", "Термос", ["T219-0001"])]
    # каталог пуст → у группы нет ни одного названия товара
    m = MasterGroupMatcher(groups, [])
    assert m.match("Термос Barkhan 1 л стальной") is None


def test_different_artikul_in_name_blocks_similar_match(matcher):
    """
    "Выжимка для цепи Barkhan S517-0045 1" и "...S517-0049 1" различаются
    только артикулом — по словам они почти идентичны, но это разные товары.
    """
    assert matcher.match("Выжимка для цепи Barkhan S517-0045 1") is None


def test_same_artikul_in_both_names_matches_even_if_wording_differs():
    groups = [_group("g", "S517-0013 Ключ", ["999", "S517-0013"])]
    products = [_product("999", "Товар S517-0013", "S517-0013")]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Ключ Barkhan S517-0013 1")["group_id"] == "g"


def test_artikul_from_linked_skus_is_last_resort(matcher):
    """
    По названиям не сходится ("Urban White" против "X147-0002"), но артикул
    напечатан прямо в названии накладной и он есть в linked_skus группы.
    """
    g = matcher.match("Barkhan мыльница X147-0002 1 шт, пластик")
    assert g["group_id"] == "g-x147-0002"


def test_inactive_group_is_ignored():
    groups = [_group("g", "K474-0007 салфетница", ["533333333", "K474-0007"], is_active=False)]
    m = MasterGroupMatcher(groups, PRODUCTS)
    assert m.match("Салфетница Облачко, пластик, металл") is None
    assert m.match("Салфетница K474-0007") is None


def test_unrelated_name_does_not_match(matcher):
    assert matcher.match("Секатор садовый 20 см") is None


def test_garbage_and_empty_names_do_not_match(matcher):
    # обрезки текстового слоя накладной не должны липнуть к группам
    for junk in [None, "", "   ", "на 10 пе", "силикон", "1 шт"]:
        assert matcher.match(junk) is None


def test_matcher_without_groups_or_catalog():
    assert MasterGroupMatcher([], PRODUCTS).match("Салфетница Облачко, пластик, металл") is None
    assert MasterGroupMatcher(GROUPS, []).match("Салфетница Облачко, пластик, металл") is None


def test_quantity_tokens_do_not_create_false_similarity():
    """"1 шт" есть почти в каждом названии — оно не должно ничего склеивать."""
    groups = [_group("g", "Свеча", ["1"])]
    products = [_product("1", "Свеча Barkhan цифра 8, 1 шт")]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Органайзер пластик 1 шт") is None


# --- Взвешивание по редкости слова, а не по его длине ---------------------
#
# Живой случай Бархана 2026-08-21 (фото накладной от владельца): на
# "Органайзер Barkhan для часов МДФ" встал штамп "X179-0014 Органайзер пластик".
# Общего у них — бренд "barkhan" (стоит почти в каждом названии витрины и ничего
# не различает) и родовое "органайзер". Верная группа проигрывала дважды: её
# название длиннее, а знаменатель max(вес накладной, вес группы) штрафовал
# именно длину.

ORGANIZER_PRODUCTS = [
    _product("147734273", "Органайзер пластик", "X179-0014"),
    _product("165794095", "Barkhan органайзер пластик", "X179-0014-1"),
    _product("105856346", "Органайзер для хранения, для 12 часов, 30х20х8см, PU, МДФ", "D618-0011"),
] + [
    # Бренд должен быть ЧАСТЫМ, как в живом каталоге Бархана — именно
    # повсеместность обнуляет его вес. На каталоге из трёх позиций "barkhan"
    # выглядел бы редким и различающим словом, чего в реальности нет.
    _product("90000%03d" % i, "Barkhan товар %d хозяйственный" % i, "Z000-%04d" % i)
    for i in range(20)
]

ORGANIZER_GROUPS = [
    _group("g-x179", "X179-0014 Органайзер пластик", ["147734273", "165794095", "X179-0014"]),
    _group("g-d618-11", "D618-0011 Органайзер для хранения для", ["105856346", "D618-0011"]),
]


def test_brand_noise_does_not_beat_meaningful_words():
    """Бренд, повторяющийся по всему каталогу, не должен перевешивать «МДФ»."""
    m = MasterGroupMatcher(ORGANIZER_GROUPS, ORGANIZER_PRODUCTS)
    assert m.match("Органайзер Barkhan для часов МДФ")["group_id"] == "g-d618-11"


def test_long_group_name_is_not_penalised_for_being_detailed():
    m = MasterGroupMatcher(ORGANIZER_GROUPS, ORGANIZER_PRODUCTS)
    g = m.match("Органайзер для хранения для 12 часов МДФ")
    assert g["group_id"] == "g-d618-11"


def test_indistinguishable_twins_get_no_stamp_instead_of_a_coin_flip():
    """
    D618-0010 «для 6 часов» и D618-0011 «для 12 часов» различаются только
    количеством. Если на накладной его нет — выбрать нельзя, и раньше
    побеждала та группа, что раньше попалась в обходе.
    """
    products = [
        _product("1", "Органайзер для хранения, для 6 часов, 30х11х8см, PU, МДФ", "D618-0010"),
        _product("2", "Органайзер для хранения, для 12 часов, 30х20х8см, PU, МДФ", "D618-0011"),
    ]
    groups = [
        _group("g-10", "D618-0010 Органайзер для хранения для", ["1", "D618-0010"]),
        _group("g-11", "D618-0011 Органайзер для хранения для", ["2", "D618-0011"]),
    ]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Органайзер Barkhan для часов МДФ") is None
    # ...но как только количество названо — двусмысленности нет.
    assert m.match("Органайзер для хранения для 12 часов 30х20х8см PU МДФ")["group_id"] == "g-11"


def test_dimensions_are_not_treated_as_article_codes():
    """
    "10x10" — габарит, а не артикул. Совпадение по артикулу проходит с мягким
    порогом, поэтому раньше любая накладная с тем же размером рисковала
    получить чужой штамп.
    """
    products = [
        _product("1", "Салфетка 10x10 см вискоза", "D697-0005"),
        _product("2", "Зеркало 10x10 см настенное", "D618-0099"),
    ]
    groups = [
        _group("g-salf", "D697-0005 Салфетка 10x10", ["1", "D697-0005"]),
        _group("g-zerk", "D618-0099 Зеркало 10x10", ["2", "D618-0099"]),
    ]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Подставка Barkhan 10x10 см металл") is None


def test_article_shared_by_two_groups_is_not_guessed():
    products = [_product("1", "Товар А", "S517-0030"), _product("2", "Товар Б", "S517-0030")]
    groups = [
        _group("g-a", "S517-0030 черный", ["1", "S517-0030"]),
        _group("g-b", "S517-0030 красный", ["2", "S517-0030"]),
    ]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Фонарь Barkhan S517-0030 налобный") is None


# --- Реальные случаи Бархана 2026-09-07: конфликт по размеру/объёму/
# количеству/цвету «размывался» на полном каталоге (1588 товаров), т.к.
# сами числа частые и почти теряют вес по IDF, а общая словесная часть
# названия — редкая и продавливает счёт выше порога. См. докстринг
# MasterGroupMatcher._similarity.


def test_differing_volume_is_rejected_despite_matching_wording():
    """Флакон 100мл X179-0013 не должен получать штамп флакона 30мл X179-0007."""
    products = [
        _product("1", "Флакон дорожный наливным путем, 30 мл, 6 шт", "X179-0007"),
        _product("2", "Флакон дорожный наливным путем, 100 мл, 5 шт", "X179-0013"),
    ]
    groups = [
        _group("g-30", "X179-0007 Флакон дорожный наливным путем", ["1", "X179-0007"]),
    ]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Флакон дорожный наливным путем, 100 мл, 5 шт") is None


def test_differing_decimal_dimension_is_rejected():
    """
    "22х9.5см" не должно распадаться на токены, которые случайно совпадут с
    "21х9.5см" — иначе два разных размера вазы делят фиктивный общий кусок.
    """
    products = [_product("1", "Ваза для цветов 21х9.5см", "D675-0011")]
    groups = [_group("g", "D675-0011 Ваза для цветов 21х9.5см", ["1", "D675-0011"])]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Ваза для цветов 22х9.5см") is None


def test_shared_dimension_but_conflicting_quantity_is_rejected():
    """
    Делят габарит "8х7.5см", но расходятся по количеству (20шт против 10шт) —
    простого isdisjoint() по измерительным токенам для этого мало.
    """
    products = [_product("1", "Barkhan Горшки для рассады 10шт, 8х7.5см, РS", "D675-0002")]
    groups = [_group("g", "D675-0002 Горшки для рассады 10шт", ["1", "D675-0002"])]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Горшки для рассады 20шт, 8х7.5см") is None


def test_conflicting_color_word_is_rejected():
    """Явно розовая мочалка не должна вставать под штамп явно синей."""
    products = [_product("1", "Barkhan Мочалка для душа, розовый, PE", "X174-0002")]
    groups = [_group("g", "X174-0002 Мочалка для душа розовый", ["1", "X174-0002"])]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Мочалка для душа, синяя, PE") is None


def test_missing_measure_or_color_is_incompleteness_not_conflict():
    """
    Название накладной короче зарегистрированного (нет размера/цвета) — это
    неполное описание, а не расхождение, штамп ставить можно.
    """
    products = [_product("1", "Barkhan Мочалка для душа, 11см, розовый, PE", "X174-0002")]
    groups = [_group("g", "X174-0002 Мочалка для душа", ["1", "X174-0002"])]
    m = MasterGroupMatcher(groups, products)
    assert m.match("Мочалка для душа")["group_id"] == "g"
