"""
Разрезы каталога по состоянию авто-демпинга: «кто упёрся в минимум», «у кого
N конкурентов», «кто стоит на максимуме», «у кого демпинг выключен, хотя
конкуренты есть» и т.п.

Почему отдельный модуль, а не пара `if` в роутере: ровно те же вычисления
нужны и списку товаров (`/api/products`), и сводке-счётчикам, и тестам. А
главное — «включён ли демпинг» здесь считается ЭФФЕКТИВНЫМ конфигом
(индивидуальный + развёрнутая поверх него настройка активной Мастер-Группы,
`dumper.config.build_effective_dumping_configs`), тем же самым кодом, что и у
движка. Иначе фильтр «демпинг выключен» показывал бы товары, которые движок в
это же время исправно демпингует по групповой настройке.

Источник данных о конкурентах — `price_war_snapshots`: их пишет piggyback'ом
сам цикл демпинга (`dumper/brain.py`), без единого лишнего запроса в Kaspi.
Отсюда важное ограничение, которое обязателен понимать и UI: расклад известен
ТОЛЬКО по тем SKU, которые хоть раз попадали в скан радара (то есть демпинг у
них когда-то был включён). Для товара, у которого демпинг не включали никогда,
`competitors_count` — не ноль, а «нет данных» (None), и путать эти два случая
нельзя. Поэтому у среза «выключен, но конкуренты есть» всегда идёт в комплекте
`checked_at`: это снимок на момент последнего скана, а не онлайн-истина.
"""

from typing import Any, Dict, List, Optional, Tuple

# Состояние самой настройки демпинга у товара.
DUMPING_STATE_ON = "ON"                          # демпинг включён (свой конфиг или группа)
DUMPING_STATE_OFF = "OFF"                        # конфиг есть, но выключен / mode=OFF
DUMPING_STATE_NO_CONFIG = "NO_CONFIG"            # настройки нет вообще

# Псевдо-срез: демпинг НЕ работает, а конкуренты по последнему скану были.
DUMPING_STATE_OFF_WITH_RIVALS = "OFF_WITH_RIVALS"

# Где цена относительно заданного коридора min/max.
PRICE_STATE_AT_MIN = "AT_MIN"
PRICE_STATE_AT_MAX = "AT_MAX"
PRICE_STATE_IN_RANGE = "IN_RANGE"
PRICE_STATE_NO_LIMITS = "NO_LIMITS"

# Наша позиция в выдаче по последнему скану.
RANK_STATE_LEADER = "LEADER"
RANK_STATE_NOT_LEADER = "NOT_LEADER"
RANK_STATE_NO_DATA = "NO_DATA"

# Цена в тенге — сравнения на равенство делаем с допуском, иначе товар,
# стоящий ровно на минимуме, из-за копеечного дребезга float то попадает в
# срез «упёрлись в минимум», то нет.
_PRICE_EPS = 0.51


def _price_of(product: dict) -> Optional[float]:
    """Текущая цена товара числом. None — если её не удалось прочитать."""
    raw = product.get("price_num")
    if raw is None:
        raw = product.get("price")
    if raw is None:
        return None
    try:
        return float(str(raw).replace("₸", "").replace("\xa0", "").replace(" ", "").strip())
    except (TypeError, ValueError):
        return None


def price_war_for_product(product: dict, price_war_by_sku: Dict[str, dict]) -> Optional[dict]:
    """
    Расклад войны по товару. Ключ снимка — тот же sku_key, которым движок
    называет оффер (merchantSku, если по нему есть конфиг, иначе sku), поэтому
    пробуем все известные псевдонимы этого физического товара.
    """
    for alias in (product.get("merchantSku"), product.get("sku"), product.get("kaspiSku")):
        if alias and str(alias) in price_war_by_sku:
            return price_war_by_sku[str(alias)]
    return None


def _dumping_state(cfg: Optional[dict]) -> str:
    if not cfg:
        return DUMPING_STATE_NO_CONFIG
    if cfg.get("enabled") and str(cfg.get("mode") or "TOP_1") != "OFF":
        return DUMPING_STATE_ON
    return DUMPING_STATE_OFF


def _price_state(price: Optional[float], cfg: Optional[dict]) -> str:
    if price is None or not cfg:
        return PRICE_STATE_NO_LIMITS
    try:
        min_price = float(cfg.get("min_price") or 0.0)
        max_price = float(cfg.get("max_price") or 0.0)
    except (TypeError, ValueError):
        return PRICE_STATE_NO_LIMITS
    if min_price <= 0 and max_price <= 0:
        return PRICE_STATE_NO_LIMITS
    # Если границы схлопнуты в одну точку (min == max), считаем это «упёрлись
    # в минимум»: для продавца это в первую очередь про потерю маржи.
    if min_price > 0 and price <= min_price + _PRICE_EPS:
        return PRICE_STATE_AT_MIN
    if max_price > 0 and price >= max_price - _PRICE_EPS:
        return PRICE_STATE_AT_MAX
    return PRICE_STATE_IN_RANGE


def _rank_state(pw: Optional[dict]) -> str:
    if not pw or pw.get("our_rank") is None:
        return RANK_STATE_NO_DATA
    return RANK_STATE_LEADER if int(pw["our_rank"]) == 1 else RANK_STATE_NOT_LEADER


def annotate_products(
    products: List[dict],
    configs: dict,
    master_groups: dict,
    price_war_items: List[dict],
) -> List[dict]:
    """
    Дописывает в каждый товар его состояние в демпинге. Мутирует товары на
    месте (они и так локальные копии кэша в роутере) и возвращает тот же
    список для удобства вызова.
    """
    from backend.dumper.config import build_effective_dumping_configs

    effective_configs, sources = build_effective_dumping_configs(products, configs, master_groups)
    price_war_by_sku = {str(it["sku"]): it for it in (price_war_items or []) if it.get("sku")}

    for p in products:
        # Матч конфига — сперва по merchantSku, потом по sku: ровно тот же
        # приоритет, что у радара (backend/dumper/radar.py).
        cfg = None
        cfg_key = None
        m_sku = p.get("merchantSku")
        if m_sku and m_sku in effective_configs:
            cfg, cfg_key = effective_configs[m_sku], m_sku
        elif p.get("sku") in effective_configs:
            cfg, cfg_key = effective_configs[p["sku"]], p["sku"]

        pw = price_war_for_product(p, price_war_by_sku)
        competitors_count = pw.get("competitors_count") if pw else None

        p["dumping_config"] = cfg
        p["dumping_config_source"] = sources.get(cfg_key) if cfg_key else None
        p["dumping_state"] = _dumping_state(cfg)
        p["price_state"] = _price_state(_price_of(p), cfg)
        p["rank_state"] = _rank_state(pw)
        # None (не пустой список полей) — честное «мы никогда не сканировали
        # этот товар», в отличие от нуля конкурентов при реальном скане.
        p["competitors_count"] = competitors_count
        p["price_war"] = (
            {
                "our_rank": pw.get("our_rank"),
                "competitors_count": pw.get("competitors_count"),
                "best_competitor_price": pw.get("best_competitor_price"),
                "best_competitor_name": pw.get("best_competitor_name"),
                "checked_at": pw.get("updated_at"),
            }
            if pw
            else None
        )

        if not cfg or not cfg.get("enabled"):
            p["dumping_status"] = "OFF"
        elif "dumping_status" not in p:
            p["dumping_status"] = "WINNING_1ST"

    return products


def _matches_dumping_state(p: dict, wanted: str) -> bool:
    state = p.get("dumping_state")
    if wanted == DUMPING_STATE_OFF_WITH_RIVALS:
        return state != DUMPING_STATE_ON and (p.get("competitors_count") or 0) > 0
    return state == wanted


def apply_dumping_filters(
    products: List[dict],
    dumping_state: str = "",
    price_state: str = "",
    rank_state: str = "",
    dumping_status_filter: str = "",
    min_competitors: Optional[int] = None,
    max_competitors: Optional[int] = None,
) -> List[dict]:
    """Все переданные условия комбинируются через И. Пустые — игнорируются."""
    out = products

    if dumping_state:
        wanted = dumping_state.strip().upper()
        out = [p for p in out if _matches_dumping_state(p, wanted)]

    if price_state:
        wanted = price_state.strip().upper()
        out = [p for p in out if p.get("price_state") == wanted]

    if rank_state:
        wanted = rank_state.strip().upper()
        out = [p for p in out if p.get("rank_state") == wanted]

    if dumping_status_filter:
        wanted = dumping_status_filter.strip().upper()
        out = [p for p in out if str(p.get("dumping_status", "")).upper() == wanted]

    if min_competitors is not None or max_competitors is not None:
        # Товары без данных скана отсекаем: у них не «0 конкурентов», у них
        # конкуренты неизвестны — молча выдавать их за ноль значит врать.
        filtered = []
        for p in out:
            cnt = p.get("competitors_count")
            if cnt is None:
                continue
            if min_competitors is not None and cnt < min_competitors:
                continue
            if max_competitors is not None and cnt > max_competitors:
                continue
            filtered.append(p)
        out = filtered

    return out


def facet_counts(products: List[dict]) -> Dict[str, Any]:
    """
    Сколько товаров попало бы в каждый срез — считается по набору ДО применения
    самих демпинг-фильтров (поиск и активен/архив уже учтены). Нужно, чтобы в
    выпадающих списках стояли живые числа и пустой результат не выглядел
    поломкой.
    """
    counts: Dict[str, int] = {
        DUMPING_STATE_ON: 0,
        DUMPING_STATE_OFF: 0,
        DUMPING_STATE_NO_CONFIG: 0,
        DUMPING_STATE_OFF_WITH_RIVALS: 0,
        PRICE_STATE_AT_MIN: 0,
        PRICE_STATE_AT_MAX: 0,
        PRICE_STATE_IN_RANGE: 0,
        PRICE_STATE_NO_LIMITS: 0,
        RANK_STATE_LEADER: 0,
        RANK_STATE_NOT_LEADER: 0,
        RANK_STATE_NO_DATA: 0,
    }
    for p in products:
        state = p.get("dumping_state")
        if state in counts:
            counts[state] += 1
        if _matches_dumping_state(p, DUMPING_STATE_OFF_WITH_RIVALS):
            counts[DUMPING_STATE_OFF_WITH_RIVALS] += 1
        ps = p.get("price_state")
        if ps in counts:
            counts[ps] += 1
        rs = p.get("rank_state")
        if rs in counts:
            counts[rs] += 1
    counts["TOTAL"] = len(products)
    return counts


def competitors_range(products: List[dict]) -> Tuple[Optional[int], Optional[int]]:
    """Мин/макс известного числа конкурентов — чтобы UI подсказал границы."""
    known = [p["competitors_count"] for p in products if p.get("competitors_count") is not None]
    if not known:
        return None, None
    return min(known), max(known)
