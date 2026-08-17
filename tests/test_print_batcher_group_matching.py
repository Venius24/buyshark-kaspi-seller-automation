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
