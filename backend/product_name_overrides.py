"""
Ручные исправления названий офферов, у которых Kaspi BFF отдаёт заглушку.

66 карточек Бархана получили `Товар <SKU>` вместо названия — не только в
принятом прайс-листе (65 из HANDOFF.md §3.67.5), но и буквально в самом поле
`title` BFF (`bff/offer-view/list`): ручная загрузка бэкапа 15.08 закрепила
заглушку в обоих местах. Проверено 2026-08-25/26: из 219 офферов с
`name == "Товар <SKU>"` в нашем кэше 66 совпадают буквально с BFF-`title`
(настоящая порча) — на них и рассчитан этот список; остальные 153 у BFF
просто честно без `title` (взять неоткуда, наш собственный фоллбек для
отображения, чинить нечего).

Здесь чинится только наш собственный сервис — что мы показываем в кабинете и
кэше; в Kaspi ничего не пишется. Идентичность в прайс-листе (`feed_identity.py`)
по-прежнему берётся из принятого файла Kaspi и этой правки не видит.

Названия сняты вручную с публичных карточек `kaspi.kz/shop/p/-{masterSku}/`
2026-08-25 — это то, что реально показывает Kaspi покупателю, независимо от
испорченного `title`/`model`.
"""

def is_placeholder_name(name, sku=None) -> bool:
    """True, если `name` — одна из наших собственных заглушек, а не реальное
    название (пустое, голое "Товар"/"Товар без названия", или "Товар <sku>")."""
    if not name:
        return True
    name = str(name).strip()
    if name in ("", "Товар", "Товар без названия"):
        return True
    if sku and name == f"Товар {sku}":
        return True
    return False


def resolve_product_name(sku, name=None, catalog_names=None):
    """Настоящее название товара или None, если взять неоткуда.

    Порядок: переданное `name`, если оно не заглушка → `catalog_names[sku]`
    (кэш каталога магазина), если тоже не заглушка → ручной список ниже.
    Вызывающий код сам решает, чем заменить None (обычно — старым значением
    или "Товар" как последним средством), см. [[project_kaspi_legacy_feed_ban]]
    и HANDOFF §3.67.5/3.83.
    """
    if not is_placeholder_name(name, sku):
        return name
    if catalog_names:
        candidate = catalog_names.get(str(sku))
        if candidate and not is_placeholder_name(candidate, sku):
            return candidate
    return KNOWN_PRODUCT_NAMES.get(str(sku)) if sku else None


# merchantSku -> настоящее название
KNOWN_PRODUCT_NAMES = {
    # данные магазина вырезаны при публикации
}
