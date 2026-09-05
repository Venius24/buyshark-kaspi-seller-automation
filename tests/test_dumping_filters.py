"""Разрезы каталога по состоянию авто-демпинга (backend/services/dumping_filters.py)."""

import pytest

from backend.services import dumping_filters as df


def _product(sku, price, merchant_sku=None, name=None, status="Активен"):
    return {
        "id": sku,
        "sku": sku,
        "merchantSku": merchant_sku,
        "name": name or f"Товар {sku}",
        "price_num": price,
        "status": status,
    }


def _cfg(min_price=0.0, max_price=0.0, enabled=True, mode="TOP_1"):
    return {
        "min_price": min_price,
        "max_price": max_price,
        "dump_step": 2,
        "enabled": enabled,
        "mode": mode,
    }


def _war(sku, competitors_count, our_rank=None):
    return {
        "sku": sku,
        "name": sku,
        "our_price": 0.0,
        "our_rank": our_rank,
        "competitors_count": competitors_count,
        "best_competitor_price": None,
        "best_competitor_name": None,
        "url": None,
        "updated_at": "2026-09-05T10:00:00",
    }


# ── Состояние настройки демпинга ───────────────────────────────────────────

def test_no_config_is_distinct_from_disabled_config():
    products = [_product("A", 1000), _product("B", 1000)]
    df.annotate_products(products, {"B": _cfg(enabled=False)}, {}, [])
    assert products[0]["dumping_state"] == df.DUMPING_STATE_NO_CONFIG
    assert products[1]["dumping_state"] == df.DUMPING_STATE_OFF


def test_mode_off_counts_as_disabled_even_when_enabled_flag_is_true():
    products = [_product("A", 1000)]
    df.annotate_products(products, {"A": _cfg(enabled=True, mode="OFF")}, {}, [])
    assert products[0]["dumping_state"] == df.DUMPING_STATE_OFF


def test_active_master_group_makes_product_count_as_dumping_on():
    """Товар без своего конфига, но внутри активной Мастер-Группы с включённым
    демпингом, движок реально демпингует — срез обязан это видеть."""
    products = [_product("A", 1000, merchant_sku="A")]
    groups = {
        "g1": {
            "is_active": True,
            "linked_skus": ["A"],
            "dumping_config": _cfg(min_price=500, max_price=1500, enabled=True),
        }
    }
    df.annotate_products(products, {}, groups, [])
    assert products[0]["dumping_state"] == df.DUMPING_STATE_ON
    assert products[0]["dumping_config_source"] == "group"


def test_sku_excluded_from_group_dumping_stays_off():
    products = [_product("A", 1000, merchant_sku="A")]
    groups = {
        "g1": {
            "is_active": True,
            "linked_skus": ["A"],
            "dumping_excluded_skus": ["A"],
            "dumping_config": _cfg(enabled=True),
        }
    }
    df.annotate_products(products, {}, groups, [])
    assert products[0]["dumping_state"] == df.DUMPING_STATE_NO_CONFIG


def test_config_matched_by_merchant_sku_first():
    """Конфиги пишутся по merchantSku — матч только по sku прятал бы их."""
    products = [_product("kaspi-1", 1000, merchant_sku="own-1")]
    df.annotate_products(products, {"own-1": _cfg(enabled=True)}, {}, [])
    assert products[0]["dumping_state"] == df.DUMPING_STATE_ON
    assert products[0]["dumping_config_source"] == "individual"


# ── Цена относительно коридора min/max ─────────────────────────────────────

@pytest.mark.parametrize(
    "price,cfg,expected",
    [
        (500.0, _cfg(min_price=500, max_price=1500), df.PRICE_STATE_AT_MIN),
        (500.4, _cfg(min_price=500, max_price=1500), df.PRICE_STATE_AT_MIN),
        (1500.0, _cfg(min_price=500, max_price=1500), df.PRICE_STATE_AT_MAX),
        (1000.0, _cfg(min_price=500, max_price=1500), df.PRICE_STATE_IN_RANGE),
        (1000.0, _cfg(min_price=0, max_price=0), df.PRICE_STATE_NO_LIMITS),
        (700.0, _cfg(min_price=700, max_price=700), df.PRICE_STATE_AT_MIN),
    ],
)
def test_price_state(price, cfg, expected):
    products = [_product("A", price)]
    df.annotate_products(products, {"A": cfg}, {}, [])
    assert products[0]["price_state"] == expected


def test_price_state_reads_formatted_price_string():
    products = [{"id": "A", "sku": "A", "name": "A", "price": "1 500 ₸", "status": "Активен"}]
    df.annotate_products(products, {"A": _cfg(min_price=500, max_price=1500)}, {}, [])
    assert products[0]["price_state"] == df.PRICE_STATE_AT_MAX


# ── Конкуренты и позиция ───────────────────────────────────────────────────

def test_competitors_count_is_none_when_never_scanned():
    """Никогда не сканированный товар — это «неизвестно», а не «0 конкурентов»."""
    products = [_product("A", 1000)]
    df.annotate_products(products, {}, {}, [])
    assert products[0]["competitors_count"] is None
    assert products[0]["price_war"] is None
    assert products[0]["rank_state"] == df.RANK_STATE_NO_DATA


def test_competitor_range_filter_excludes_unscanned_products():
    products = [_product("A", 1000), _product("B", 1000), _product("C", 1000)]
    df.annotate_products(products, {}, {}, [_war("A", 0), _war("B", 5)])
    assert [p["sku"] for p in df.apply_dumping_filters(products, min_competitors=1)] == ["B"]
    assert [p["sku"] for p in df.apply_dumping_filters(products, max_competitors=0)] == ["A"]
    # C нигде: у него конкуренты неизвестны, выдавать его за ноль нельзя.
    both = df.apply_dumping_filters(products, min_competitors=0, max_competitors=99)
    assert [p["sku"] for p in both] == ["A", "B"]


def test_price_war_matched_through_merchant_sku_alias():
    products = [_product("kaspi-1", 1000, merchant_sku="own-1")]
    df.annotate_products(products, {}, {}, [_war("own-1", 3, our_rank=2)])
    assert products[0]["competitors_count"] == 3
    assert products[0]["rank_state"] == df.RANK_STATE_NOT_LEADER
    assert products[0]["price_war"]["checked_at"] == "2026-09-05T10:00:00"


def test_rank_state_leader():
    products = [_product("A", 1000)]
    df.annotate_products(products, {}, {}, [_war("A", 4, our_rank=1)])
    assert products[0]["rank_state"] == df.RANK_STATE_LEADER


# ── Главный запрошенный срез ───────────────────────────────────────────────

def test_off_with_rivals_finds_disabled_and_unconfigured_products_with_competitors():
    products = [
        _product("ON_WITH_RIVALS", 1000),
        _product("OFF_WITH_RIVALS", 1000),
        _product("NO_CFG_WITH_RIVALS", 1000),
        _product("OFF_NO_RIVALS", 1000),
    ]
    configs = {
        "ON_WITH_RIVALS": _cfg(enabled=True),
        "OFF_WITH_RIVALS": _cfg(enabled=False),
        "OFF_NO_RIVALS": _cfg(enabled=False),
    }
    war = [
        _war("ON_WITH_RIVALS", 3, our_rank=1),
        _war("OFF_WITH_RIVALS", 2, our_rank=4),
        _war("NO_CFG_WITH_RIVALS", 7, our_rank=9),
        _war("OFF_NO_RIVALS", 0, our_rank=1),
    ]
    df.annotate_products(products, configs, {}, war)

    got = df.apply_dumping_filters(products, dumping_state=df.DUMPING_STATE_OFF_WITH_RIVALS)
    assert sorted(p["sku"] for p in got) == ["NO_CFG_WITH_RIVALS", "OFF_WITH_RIVALS"]


def test_group_dumped_product_is_not_reported_as_off_with_rivals():
    """Регресс: групповой демпинг раньше не учитывался, и такой товар попадал
    бы в срез «выключен, хотя конкуренты есть», хотя движок его демпингует."""
    products = [_product("A", 1000, merchant_sku="A")]
    groups = {"g1": {"is_active": True, "linked_skus": ["A"], "dumping_config": _cfg(enabled=True)}}
    df.annotate_products(products, {}, groups, [_war("A", 5, our_rank=3)])
    assert df.apply_dumping_filters(products, dumping_state=df.DUMPING_STATE_OFF_WITH_RIVALS) == []


# ── Комбинирование и счётчики ──────────────────────────────────────────────

def test_filters_combine_with_and():
    products = [_product("A", 500), _product("B", 500), _product("C", 1500)]
    configs = {s: _cfg(min_price=500, max_price=1500) for s in ("A", "B", "C")}
    df.annotate_products(products, configs, {}, [_war("A", 6, 2), _war("B", 1, 2), _war("C", 6, 2)])

    got = df.apply_dumping_filters(
        products,
        dumping_state=df.DUMPING_STATE_ON,
        price_state=df.PRICE_STATE_AT_MIN,
        rank_state=df.RANK_STATE_NOT_LEADER,
        min_competitors=5,
    )
    assert [p["sku"] for p in got] == ["A"]


def test_empty_filters_are_a_no_op():
    products = [_product("A", 1000), _product("B", 1000)]
    df.annotate_products(products, {}, {}, [])
    assert df.apply_dumping_filters(products) == products


def test_facet_counts_cover_every_bucket():
    products = [_product("A", 500), _product("B", 1000), _product("C", 1000)]
    configs = {"A": _cfg(min_price=500, max_price=1500), "B": _cfg(enabled=False)}
    df.annotate_products(products, configs, {}, [_war("A", 2, 1), _war("B", 3, 5)])
    counts = df.facet_counts(products)

    assert counts["TOTAL"] == 3
    assert counts[df.DUMPING_STATE_ON] == 1
    assert counts[df.DUMPING_STATE_OFF] == 1
    assert counts[df.DUMPING_STATE_NO_CONFIG] == 1
    assert counts[df.DUMPING_STATE_OFF_WITH_RIVALS] == 1  # только B: у C нет данных
    assert counts[df.PRICE_STATE_AT_MIN] == 1
    assert counts[df.RANK_STATE_LEADER] == 1
    assert counts[df.RANK_STATE_NOT_LEADER] == 1
    assert counts[df.RANK_STATE_NO_DATA] == 1


def test_competitors_range():
    products = [_product("A", 1000), _product("B", 1000), _product("C", 1000)]
    df.annotate_products(products, {}, {}, [_war("A", 2), _war("B", 9)])
    assert df.competitors_range(products) == (2, 9)
    assert df.competitors_range([_product("Z", 1)]) == (None, None)
