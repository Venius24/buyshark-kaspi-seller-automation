"""
Рецепт списания артикула: из каких Мастер-Групп и сколько штук уходит за
ОДНУ проданную единицу товара.

Зачем
-----
Раньше размер комплекта задавался на всю группу (`deduct_per_unit`), а в
одной группе лежат и одиночные карточки, и двойные: `H422-0007-3` — одна
подставка, `H422-0007-9 2шт` — две. Поставить группе ×2 значило списывать две
за одиночную, поэтому владелец заводил под двойные отдельную группу со своим
остатком — два счётчика на одну полку. Второй случай — набор из разных
товаров: `S517-0046-48` («Набор для ремонта», съёмник S517-0046 + ключ
S517-0048) лежал только в группе 46, продажа не трогала 48, а на витрине у
набора висел остаток съёмников (~329) при 88 ключах.

Модель
------
Рецепт хранится на «родной» группе артикула (той, в чьих linked_skus он
лежит — так матчинг заказа остаётся прежним), полем `sku_recipes`:

    {"S517-0046-48": {"qty": 1, "extra": [{"group_id": "grp_48", "qty": 1}]},
     "H422-0007-9 2шт": {"qty": 2}}

`qty` — сколько штук своей группы, `extra` — другие группы набора. Артикула
нет в словаре — значит «своя группа × 1», то есть ровно прежнее поведение.

Остаток на витрине считается по самой бедной составляющей:
min(остаток группы // qty), и 0, если хоть одна составляющая на своём
стоп-количестве или ниже — набор без ключа продавать нельзя.

Артикул в linked_skus ДВУХ групп без рецепта по-прежнему считается
неоднозначным и не списывается (match_group_for_sku): набор заводится
только явно, иначе опечатка в привязках молча списывала бы из обеих групп.
"""
from typing import Dict, Iterable, List, Optional, Tuple

MAX_QTY = 1000


def _canon(raw) -> str:
    """Артикул как он записан, без регистра и лишних пробелов.

    То же правило, что stock_sync_worker._canon_sku (отдельной копией, чтобы
    воркер мог импортировать этот модуль без цикла).
    """
    return " ".join(str(raw or "").split()).lower()


def _first_token(raw) -> str:
    s = _canon(raw)
    return s.split()[0] if s else ""


def _qty(raw) -> int:
    try:
        return max(1, int(raw or 1))
    except (TypeError, ValueError):
        return 1


def entry_for(group: Optional[dict], sku) -> Optional[dict]:
    """Запись рецепта артикула в группе, либо None.

    Сначала артикул как есть (вместе с припиской «2шт» — это РАЗНЫЕ товары,
    см. §3.49), потом без приписки — для заказа, где продавец дописал хвост,
    которого нет в ключе.
    """
    recipes = (group or {}).get("sku_recipes") or {}
    if not recipes:
        return None
    key = _canon(sku)
    if not key:
        return None
    for k, v in recipes.items():
        if _canon(k) == key:
            return v
    token = _first_token(sku)
    for k, v in recipes.items():
        if _canon(k) == token:
            return v
    return None


def components_for(home_gid: str, groups: dict, sku) -> List[Tuple[str, int]]:
    """[(group_id, штук за 1 продажу)] — своя группа всегда первой."""
    entry = entry_for(groups.get(home_gid), sku)
    if not entry:
        return [(home_gid, 1)]
    comps = [(home_gid, _qty(entry.get("qty")))]
    seen = {home_gid}
    for x in entry.get("extra") or []:
        gid = (x or {}).get("group_id")
        if gid and gid not in seen:
            seen.add(gid)
            comps.append((gid, _qty(x.get("qty"))))
    return comps


def is_plain(components: List[Tuple[str, int]]) -> bool:
    return len(components) == 1 and components[0][1] == 1


def sellable(components: Iterable[Tuple[str, int]], groups: dict) -> Tuple[int, int]:
    """(сколько наборов собирается из остатка, сколько выставлять на Kaspi).

    Первое число — для кэша каталога и оверрайда, второе — с учётом стоп-кол-ва
    каждой составляющей. Выключенная или удалённая группа-составляющая
    остатком не управляется и набор не ограничивает.

    Для обычного артикула (своя группа × 1) это ровно прежнее правило:
    (shared_stock, shared_stock если > stop_qty, иначе 0).
    """
    stock_vals, push_vals = [], []
    for gid, qty in components:
        g = groups.get(gid)
        if not g or not g.get("is_active", True):
            continue
        shared = max(0, int(g.get("shared_stock", 0) or 0))
        stop = int(g.get("stop_qty", 5) if g.get("stop_qty") is not None else 5)
        avail = shared // _qty(qty)
        stock_vals.append(avail)
        push_vals.append(avail if shared > stop else 0)
    if not stock_vals:
        return 0, 0
    return min(stock_vals), min(push_vals)


def bundles_touching(gid: str, groups: dict) -> List[Tuple[str, str]]:
    """[(родная группа, артикул)] наборов из ДРУГИХ групп, куда входит gid.

    Нужны рассылке: остаток ключей S517-0048 изменился — пересчитать надо и
    набор S517-0046-48, хотя в linked_skus группы 48 его нет.
    """
    out = []
    for home_gid, g in groups.items():
        if home_gid == gid or not g.get("is_active", True):
            continue
        for sku, entry in (g.get("sku_recipes") or {}).items():
            if any((x or {}).get("group_id") == gid for x in (entry or {}).get("extra") or []):
                out.append((home_gid, sku))
    return out


def clean_recipes(home_gid: str, recipes: Optional[dict], groups: dict,
                  linked_skus: Iterable[str], strict: bool = True) -> Dict[str, dict]:
    """Проверка и нормализация рецептов перед сохранением группы.

    Бросает ValueError с человеческим текстом, если рецепт ссылается на чужую
    или несуществующую группу (strict=False — молча выбрасывает такую
    составляющую: так чистятся уже сохранённые рецепты, когда пользователь
    правит в группе что-то другое). Рецепты артикулов, отвязанных от группы,
    молча выпадают, как и записи «×1 без набора» — это и так поведение по
    умолчанию, хранить его незачем.
    """
    linked = {_canon(s) for s in linked_skus or []}
    out: Dict[str, dict] = {}
    for sku, entry in (recipes or {}).items():
        key = " ".join(str(sku or "").split())
        if not key or _canon(key) not in linked:
            continue
        entry = entry or {}
        qty = _check_qty(entry.get("qty"), key)
        extra, seen = [], {home_gid}
        for x in entry.get("extra") or []:
            gid = (x or {}).get("group_id")
            if not gid:
                continue
            problem = None
            if gid == home_gid:
                problem = f"Набор {key}: своя группа уже входит в набор, её количество задаётся полем ×N."
            elif gid in seen:
                problem = f"Набор {key}: группа указана дважды."
            elif gid not in groups:
                problem = f"Набор {key}: группа {gid} не найдена в этом магазине."
            if problem:
                if strict:
                    raise ValueError(problem)
                continue
            seen.add(gid)
            extra.append({"group_id": gid, "qty": _check_qty(x.get("qty"), key)})
        if qty == 1 and not extra:
            continue
        out[key] = {"qty": qty, **({"extra": extra} if extra else {})}
    return out


def _check_qty(raw, sku: str) -> int:
    try:
        n = int(raw if raw is not None else 1)
    except (TypeError, ValueError):
        raise ValueError(f"{sku}: количество должно быть целым числом.")
    if n < 1 or n > MAX_QTY:
        raise ValueError(f"{sku}: количество должно быть от 1 до {MAX_QTY}.")
    return n


def drop_group_refs(groups: dict, gid: str) -> Dict[str, dict]:
    """Убирает удалённую группу из наборов других групп; возвращает изменённые."""
    changed = {}
    for home_gid, g in groups.items():
        recipes = g.get("sku_recipes") or {}
        touched = False
        for entry in recipes.values():
            extra = (entry or {}).get("extra") or []
            kept = [x for x in extra if (x or {}).get("group_id") != gid]
            if len(kept) != len(extra):
                touched = True
                if kept:
                    entry["extra"] = kept
                else:
                    entry.pop("extra", None)
        if touched:
            g["sku_recipes"] = {k: v for k, v in recipes.items()
                                if _qty(v.get("qty")) > 1 or v.get("extra")}
            changed[home_gid] = g
    return changed
