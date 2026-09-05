"""
Тесты прогноза остатков (backend/services/stock_forecast.py).

Проверяется ядро build_forecast — оно чистое и не ходит в БД, поэтому весь
сценарий задаётся данными: каталог, мастер-группы, продажи по дням.

Главные свойства, ради которых тесты вообще написаны:

1. Скорость считается от ПЕРВОЙ продажи, а не от начала окна — иначе товар,
   выставленный три дня назад, выглядит «спящим» и никогда не попадает в
   список «скоро кончится».
2. Мастер-группа — одна строка на общий остаток, а не N строк участников.
   Иначе запас по группе завышается ровно во столько раз, сколько в ней
   привязано артикулов.
3. «Сегодня» в окно не входит: неполные сутки роняли бы скорость всего
   каталога каждое утро.
"""
import datetime as dt

import pytest

from backend.services.stock_forecast import (
    build_forecast,
    build_purchase_order_xlsx,
)

TODAY = dt.date(2026, 9, 5)


def _product(m_sku, stock=100, price=1000, name=None, status="Активен", sku=None):
    return {
        "merchantSku": m_sku,
        "sku": sku or f"1100{abs(hash(m_sku)) % 10000:04d}",
        "name": name or f"Товар {m_sku}",
        "price_num": price,
        "stock": stock,
        "status": status,
        "image": None,
    }


def _daily(m_sku, per_day, days, end_offset=1):
    """{m_sku: {день: qty}} — `days` подряд идущих дней по `per_day` штук,
    заканчивая (TODAY - end_offset)."""
    last = TODAY - dt.timedelta(days=end_offset)
    return {m_sku: {last - dt.timedelta(days=i): per_day for i in range(days)}}


def _by_key(result):
    return {it["key"]: it for it in result["items"]}


def test_velocity_and_days_to_zero_basic():
    """2 шт/день ровно 30 дней, остаток 60 -> 30 дней до нуля."""
    res = build_forecast(
        products=[_product("A-1", stock=60)],
        groups={},
        daily_by_sku=_daily("A-1", 2, 30),
        today=TODAY,
    )
    item = _by_key(res)["A-1"]
    assert item["velocity"] == pytest.approx(2.0)
    assert item["days_to_zero"] == pytest.approx(30.0)
    assert item["bucket"] == "ok"


def test_new_product_velocity_counts_from_first_sale():
    """Товар начал продаваться 3 дня назад: 5 шт/день. Окно — 60 дней, но
    делить на 60 нельзя, иначе скорость 0.25 вместо 5 и остаток 10 штук
    выглядит как запас на 40 дней вместо двух."""
    res = build_forecast(
        products=[_product("NEW-1", stock=10)],
        groups={},
        daily_by_sku=_daily("NEW-1", 5, 3),
        today=TODAY,
    )
    item = _by_key(res)["NEW-1"]
    assert item["velocity"] == pytest.approx(5.0)
    assert item["days_to_zero"] == pytest.approx(2.0)
    assert item["bucket"] == "critical"


def test_current_day_excluded_from_window():
    """Продажи только за сегодня в скорость не идут — сутки ещё не закрыты."""
    res = build_forecast(
        products=[_product("TODAY-1", stock=10)],
        groups={},
        daily_by_sku={"TODAY-1": {TODAY: 50}},
        today=TODAY,
    )
    item = _by_key(res)["TODAY-1"]
    assert item["sold"] == 0
    assert item["velocity"] == 0.0


def test_recent_trend_outweighs_old_history():
    """Товар два месяца шёл по 1 шт/день, последние две недели — по 6.
    Итоговая скорость обязана быть заметно ближе к свежему тренду, иначе
    заявка будет посчитана по мёртвой средней."""
    daily = {}
    daily.update(_daily("HOT-1", 1, 45, end_offset=15)["HOT-1"])
    daily.update(_daily("HOT-1", 6, 14, end_offset=1)["HOT-1"])
    res = build_forecast(
        products=[_product("HOT-1", stock=100)],
        groups={},
        daily_by_sku={"HOT-1": daily},
        today=TODAY,
    )
    item = _by_key(res)["HOT-1"]
    assert item["velocity_recent"] == pytest.approx(6.0)
    assert item["velocity_long"] < 3.0
    assert item["velocity"] > item["velocity_long"]
    assert item["velocity"] == pytest.approx(0.6 * item["velocity_recent"] + 0.4 * item["velocity_long"], rel=1e-3)


def test_master_group_is_one_row_on_shared_stock():
    """Три артикула делят один остаток 90. Каждый продаёт 1 шт/день.
    Правильный ответ — одна строка со скоростью 3 и 30 днями до нуля, а не
    три строки по 90 дней каждая."""
    groups = {
        "grp_1": {
            "group_id": "grp_1",
            "group_name": "Термосумка 3 л",
            "shared_stock": 90,
            "linked_skus": ["G-1", "G-2", "G-3"],
            "is_active": True,
            "deduct_per_unit": 1,
            "external_code": "1C-778",
        }
    }
    daily = {}
    for s in ("G-1", "G-2", "G-3"):
        daily.update(_daily(s, 1, 30))
    res = build_forecast(
        products=[_product(s, stock=90) for s in ("G-1", "G-2", "G-3")],
        groups=groups,
        daily_by_sku=daily,
        today=TODAY,
    )
    keys = _by_key(res)
    assert set(keys) == {"grp_1"}
    grp = keys["grp_1"]
    assert grp["kind"] == "group"
    assert grp["members_count"] == 3
    assert grp["velocity"] == pytest.approx(3.0)
    assert grp["days_to_zero"] == pytest.approx(30.0)
    assert grp["external_code"] == "1C-778"


def test_master_group_deduct_per_unit_multiplies_velocity():
    """Комплект из 4 штук: одна продажа съедает 4 единицы общего остатка —
    то же правило, по которому списывает stock_sync_worker."""
    groups = {
        "grp_1": {
            "group_id": "grp_1", "group_name": "Палочки", "shared_stock": 80,
            "linked_skus": ["K-1"], "is_active": True, "deduct_per_unit": 4,
        }
    }
    res = build_forecast(
        products=[_product("K-1", stock=80)],
        groups=groups,
        daily_by_sku=_daily("K-1", 1, 30),
        today=TODAY,
    )
    grp = _by_key(res)["grp_1"]
    assert grp["velocity"] == pytest.approx(4.0)
    assert grp["days_to_zero"] == pytest.approx(20.0)


def test_inactive_group_members_are_forecast_individually():
    """Выключенная группа остаток не транслирует — участники считаются
    каждый по своему складскому числу."""
    groups = {
        "grp_off": {
            "group_id": "grp_off", "group_name": "Выключена", "shared_stock": 90,
            "linked_skus": ["G-1", "G-2"], "is_active": False, "deduct_per_unit": 1,
        }
    }
    res = build_forecast(
        products=[_product("G-1", stock=10), _product("G-2", stock=10)],
        groups=groups,
        daily_by_sku={**_daily("G-1", 1, 30), **_daily("G-2", 1, 30)},
        today=TODAY,
    )
    keys = _by_key(res)
    assert set(keys) == {"G-1", "G-2"}
    assert keys["G-1"]["days_to_zero"] == pytest.approx(10.0)


def test_buckets_and_summary():
    products = [
        _product("ZERO-1", stock=0),      # продавался, кончился
        _product("CRIT-1", stock=6),      # 3 дня до нуля
        _product("WARN-1", stock=20),     # 10 дней: позже недели, раньше поставки
        _product("OK-1", stock=200),      # 100 дней
        _product("DEAD-1", stock=50),     # ни одной продажи
    ]
    daily = {}
    for s in ("ZERO-1", "CRIT-1", "WARN-1", "OK-1"):
        daily.update(_daily(s, 2, 30))
    res = build_forecast(
        products=products, groups={}, daily_by_sku=daily, today=TODAY,
        lead_time_days=14, cover_days=14, horizon_days=7,
    )
    keys = _by_key(res)
    assert keys["ZERO-1"]["bucket"] == "out_of_stock"
    assert keys["CRIT-1"]["bucket"] == "critical"
    assert keys["WARN-1"]["bucket"] == "warning"
    assert keys["OK-1"]["bucket"] == "ok"
    assert keys["DEAD-1"]["bucket"] == "dead"

    summary = res["summary"]
    assert summary["out_of_stock"] == 1
    assert summary["critical"] == 1
    assert summary["warning"] == 1
    assert summary["dead"] == 1
    # Замороженные деньги: 50 шт по 1000 ₸
    assert summary["frozen_cost"] == pytest.approx(50_000)


def test_recommended_qty_covers_lead_time_plus_cover():
    """2 шт/день, поставка 14 дней, запас на 14 дней -> целевой запас 56 штук.
    На складе 6 -> заказать 50."""
    res = build_forecast(
        products=[_product("R-1", stock=6)],
        groups={}, daily_by_sku=_daily("R-1", 2, 30), today=TODAY,
        lead_time_days=14, cover_days=14,
    )
    item = _by_key(res)["R-1"]
    assert item["recommended_qty"] == 50
    assert item["reorder_cost"] == pytest.approx(50_000)


def test_no_reorder_when_stock_already_covers_target():
    res = build_forecast(
        products=[_product("FULL-1", stock=500)],
        groups={}, daily_by_sku=_daily("FULL-1", 1, 30), today=TODAY,
    )
    assert _by_key(res)["FULL-1"]["recommended_qty"] == 0


def test_archived_without_sales_is_dropped_but_archived_with_sales_kept():
    """Снятых карточек в каталоге сотни — без продаж им в отчёте о закупе
    делать нечего. А вот снятая карточка, которая ещё продавалась в окне, —
    это как раз потерянные продажи, её показываем."""
    res = build_forecast(
        products=[
            _product("ARC-DEAD", stock=5, status="Архив"),
            _product("ARC-SOLD", stock=0, status="Архив"),
        ],
        groups={}, daily_by_sku=_daily("ARC-SOLD", 3, 20), today=TODAY,
    )
    keys = _by_key(res)
    assert "ARC-DEAD" not in keys
    assert keys["ARC-SOLD"]["bucket"] == "out_of_stock"


def test_sku_sold_but_missing_from_catalog_is_not_a_stockout():
    """Артикул из истории продаж, которого нет в каталоге.

    Живьём на Бархане таких нашлось 53 — это старые написания артикулов с
    лишними нулями ("X147-00006" при живом "X147-0006"), под которыми товар
    продавался до переименования. Складского остатка у них нет в принципе,
    поэтому в «кончился» они попадали автоматически и раздували главную
    цифру отчёта со 94 до 147. В вёдра они не идут — но и молча пропасть не
    должны: их количество уезжает в summary отдельной строкой.
    """
    res = build_forecast(
        products=[_product("REAL-1", stock=100)],
        groups={},
        daily_by_sku={**_daily("GHOST-1", 1, 10), **_daily("REAL-1", 1, 30)},
        today=TODAY,
    )
    assert "GHOST-1" not in _by_key(res)
    assert res["summary"]["out_of_stock"] == 0
    assert res["summary"]["unmatched_skus"] == 1
    assert res["summary"]["unmatched_sold"] == 10


def test_zero_stock_has_no_runout_date():
    """У позиции в нуле даты «закончится» быть не может — она уже кончилась.
    Раньше сюда попадало сегодняшнее число, и в заявке поставщику стояло
    «закончится сегодня» у товара, который стоит в нуле неделю."""
    res = build_forecast(
        products=[_product("Z-1", stock=0), _product("L-1", stock=60)],
        groups={},
        daily_by_sku={**_daily("Z-1", 2, 30), **_daily("L-1", 2, 30)},
        today=TODAY,
    )
    keys = _by_key(res)
    assert keys["Z-1"]["runs_out_on"] is None
    assert keys["L-1"]["runs_out_on"] == (TODAY + dt.timedelta(days=30)).isoformat()


def test_archived_with_sales_at_zero_is_the_real_stockout():
    """Снятая с витрины карточка, которая ещё продавалась и стоит в нуле, —
    это ровно «кончилось, поэтому и сняли». Самая ценная строка отчёта."""
    res = build_forecast(
        products=[_product("ARC-0", stock=0, status="Архив")],
        groups={}, daily_by_sku=_daily("ARC-0", 3, 20), today=TODAY,
    )
    item = _by_key(res)["ARC-0"]
    assert item["bucket"] == "out_of_stock"
    assert item["status"] == "Архив"
    assert item["recommended_qty"] > 0


def test_items_sorted_by_urgency():
    products = [_product("SLOW-1", stock=300), _product("URGENT-1", stock=4)]
    daily = {**_daily("SLOW-1", 1, 30), **_daily("URGENT-1", 2, 30)}
    res = build_forecast(products=products, groups={}, daily_by_sku=daily, today=TODAY)
    assert res["items"][0]["key"] == "URGENT-1"
    # Позиции без скорости — всегда в конце
    assert res["items"][-1]["days_to_zero"] is None or res["items"][-1]["key"] != "URGENT-1"


def test_max_items_keeps_all_actionable_rows():
    """Урезание items (дашборд опрашивает /stock-forecast каждые 5 минут —
    большой каталог не должен весить мегабайты на каждый опрос) не имеет
    права потерять ни одну позицию, требующую внимания: ни «горящую»
    (out_of_stock/critical/warning), ни ту, что реально в заявке
    (recommended_qty > 0), даже если бюджет max_items меньше их числа."""
    products = [_product(f"CRIT-{i}", stock=6) for i in range(5)]
    products += [_product(f"OK-{i}", stock=500) for i in range(20)]
    daily = {}
    for i in range(5):
        daily.update(_daily(f"CRIT-{i}", 2, 30))
    for i in range(20):
        daily.update(_daily(f"OK-{i}", 1, 30))

    res = build_forecast(products=products, groups={}, daily_by_sku=daily, today=TODAY, max_items=10)

    assert res["items_truncated"] is True
    keys = {it["key"] for it in res["items"]}
    assert {f"CRIT-{i}" for i in range(5)} <= keys
    assert len(res["items"]) == 10
    # summary считается по ПОЛНОМУ набору, не по урезанному
    assert res["summary"]["total_items"] == 25
    assert res["summary"]["critical"] == 5


def test_max_items_noop_when_under_budget():
    res = build_forecast(
        products=[_product("A-1", stock=60)], groups={}, daily_by_sku=_daily("A-1", 2, 30),
        today=TODAY, max_items=500,
    )
    assert res["items_truncated"] is False
    assert len(res["items"]) == 1


def test_purchase_order_xlsx_has_only_reorder_rows():
    openpyxl = pytest.importorskip("openpyxl")
    res = build_forecast(
        products=[_product("R-1", stock=6), _product("FULL-1", stock=500)],
        groups={},
        daily_by_sku={**_daily("R-1", 2, 30), **_daily("FULL-1", 1, 30)},
        today=TODAY,
    )
    data = build_purchase_order_xlsx(res, only_reorder=True)
    assert data[:2] == b"PK"

    import io as _io
    wb = openpyxl.load_workbook(_io.BytesIO(data))
    ws = wb.active
    skus = [row[0] for row in ws.iter_rows(min_row=4, values_only=True) if row[0]]
    assert "R-1" in skus
    assert "FULL-1" not in skus
