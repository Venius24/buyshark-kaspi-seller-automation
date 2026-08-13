"""
Чистая логика маппинга и матчинга для импорта настроек демпинга из
PriceFeed.kz — без I/O, без сети, без браузера. Основная тестовая ценность
фичи живёт здесь (см. tests/test_pricefeed_mapping.py).

Вход — сырые элементы content[] от GET .../city-prices (см.
pricefeed_client.py и REAL_IMPORTANT/импорт_pricefeed/PRICEFEED_IMPORT.md),
выход — готовые к записи в dumping_configs словари, совместимые по форме с
тем, что уже пишет POST /config (backend/routers/dumping.py:136-154).
"""

from dataclasses import dataclass, field

# Сверено с backend/dumper/brain.py:147-206. OFF выключает демпинг, а не
# маппится в какой-то "выключенный режим" — enabled=False держит прежний
# mode нетронутым при мерже с уже существующим конфигом.
ALGORITHM_TO_MODE = {
    "BEST_PRICE_MINUS_STEP": "TOP_1",
    "BEST_PRICE": "MATCH_FIRST",
    "NEXT_PRICE_MINUS_STEP": "TOP_2",
    "BEST_PRICE_PLUS_STEP": "TOP_2",
}
# Точно не 1-в-1 воспроизводят логику PriceFeed нашими режимами — предупреждаем
# в превью, а не молчим.
INEXACT_ALGORITHMS = {"BEST_PRICE", "BEST_PRICE_PLUS_STEP"}

_SKU_FIELDS = ("sku", "merchantSku", "masterSku", "kaspiSku")

_DEFAULT_CONFIG = {
    "dump_step": 2.0,
    "enabled": True,
    "mode": "TOP_1",
    "target_position": 1,
    "max_delivery_days": None,
    "min_seller_rating": None,
    "same_city_only": False,
    "blacklisted_merchants": [],
    "city_code": "750000000",
}


@dataclass
class MatchReport:
    rows: dict = field(default_factory=dict)  # {canonical_sku: config_dict}
    already_configured: set = field(default_factory=set)
    unmatched: list = field(default_factory=list)  # sku из PriceFeed, не найденные в каталоге
    skipped: dict = field(default_factory=dict)  # {reason: count}
    warnings: list = field(default_factory=list)


def _norm(value) -> str:
    return str(value).strip().lower() if value not in (None, "") else ""


def map_item(item: dict):
    """Маппит один сырой элемент PriceFeed в наш конфиг-словарь.

    Возвращает (config_dict_без_sku, None) при успехе либо
    (None, причина_пропуска) при пропуске. Причина — один из:
    no_min_or_max, min_gte_max, algorithm_off_no_limits, unknown_algorithm.
    """
    algorithm = item.get("algorithm")
    min_price = item.get("minPrice")
    max_price = item.get("maxPrice")

    if algorithm == "OFF":
        return None, "algorithm_off_no_limits"

    if algorithm not in ALGORITHM_TO_MODE:
        return None, "unknown_algorithm"

    if min_price in (None, 0) or max_price in (None, 0):
        return None, "no_min_or_max"

    try:
        min_price = float(min_price)
        max_price = float(max_price)
    except (TypeError, ValueError):
        return None, "no_min_or_max"

    if min_price >= max_price:
        return None, "min_gte_max"

    step = item.get("step")
    try:
        step = float(step)
    except (TypeError, ValueError):
        step = 0.0
    if step <= 0:
        step = 2.0

    config = dict(_DEFAULT_CONFIG)
    config.update({
        "min_price": min_price,
        "max_price": max_price,
        "dump_step": step,
        "enabled": True,
        "mode": ALGORITHM_TO_MODE[algorithm],
    })
    return config, None


def build_sku_index(products: list) -> dict:
    """{нормализованный_sku: канонический_sku_для_dumping_configs}.

    Канонический sku — merchantSku, если есть, иначе sku (то же, что
    означает DumpingProductConfig.sku, backend/dumper/config.py:52)."""
    index = {}
    for p in products:
        canonical = p.get("merchantSku") or p.get("sku")
        if not canonical:
            continue
        canonical = str(canonical).strip()
        for field_name in _SKU_FIELDS:
            val = _norm(p.get(field_name))
            if val:
                index.setdefault(val, canonical)
    return index


def match_items(items: list, products: list, existing_configs: dict) -> MatchReport:
    """Собирает готовый к записи набор конфигов + отчёт по пропускам.

    items — сырые элементы PriceFeed city-prices; products — каталог
    магазина (_load_products); existing_configs — текущие dumping_configs
    (_load_dumping_configs), нужны только чтобы посчитать already_configured
    и (на стороне роутера) смержить при перезаписи."""
    report = MatchReport()
    sku_index = build_sku_index(products)
    seen_canonical = set()
    duplicate_skus: list = []

    for item in items:
        config, skip_reason = map_item(item)
        if skip_reason is not None:
            report.skipped[skip_reason] = report.skipped.get(skip_reason, 0) + 1
            continue

        pf_sku = _norm(item.get("sku"))
        pf_master = _norm(item.get("masterSku"))
        canonical = sku_index.get(pf_sku) or sku_index.get(pf_master)

        if not canonical:
            report.unmatched.append(item.get("sku") or item.get("masterSku") or "?")
            report.skipped["not_in_catalog"] = report.skipped.get("not_in_catalog", 0) + 1
            continue

        if canonical in seen_canonical:
            report.skipped["duplicate_sku"] = report.skipped.get("duplicate_sku", 0) + 1
            duplicate_skus.append(canonical)
            continue
        seen_canonical.add(canonical)

        config["sku"] = canonical
        config["_source_title"] = item.get("masterTitle")
        if canonical in existing_configs:
            report.already_configured.add(canonical)
        report.rows[canonical] = config

    if duplicate_skus:
        # ОДНА строка на все дубли, а не по строке на каждый: в реальной выгрузке
        # их сотни, и раньше этот список раздувал превью так, что модалка
        # переставала помещаться на экран и страница не листалась.
        examples = ", ".join(duplicate_skus[:5])
        tail = f" и ещё {len(duplicate_skus) - 5}" if len(duplicate_skus) > 5 else ""
        report.warnings.append(
            f"Дубликат SKU в выгрузке PriceFeed: {len(duplicate_skus)} шт "
            f"({examples}{tail}) — по каждому оставлена первая запись."
        )

    unknown_count = report.skipped.get("unknown_algorithm", 0)
    if unknown_count:
        report.warnings.append(
            f"{unknown_count} товар(ов) с незнакомым алгоритмом PriceFeed — пропущены, не импортированы."
        )

    return report


def merge_with_existing(new_config: dict, existing: dict) -> dict:
    """При перезаписи сохраняем поля, которых нет в PriceFeed
    (city_code, target_position, max_delivery_days, min_seller_rating,
    same_city_only, blacklisted_merchants) — _save_dumping_configs пишет
    все колонки, наивная запись только min/max/step/mode/enabled их бы
    обнулила."""
    merged = dict(existing)
    merged.update({
        "min_price": new_config["min_price"],
        "max_price": new_config["max_price"],
        "dump_step": new_config["dump_step"],
        "enabled": new_config["enabled"],
        "mode": new_config["mode"],
    })
    return merged
