"""
Кэш разобранных sales_orders (backend/services/sales_orders_cache.py) и
остальные правки скорости дашборда 2026-10-18: дневная агрегация авто-чата в
БД, журнал синка мастер-групп по запросу, TTL лидерборда конкурентов.

Главное, что проверяем у кэша: ответы те же, что без него. Новый заказ,
записанный синком (_upsert_sales_orders), обязан быть виден следующему
вызову, а не через TTL.
"""
import datetime as _dt
import json
import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def db(tmp_path):
    import backend.db.models  # noqa: F401
    from backend.db.database import Base
    from backend import state_helpers

    path = str(tmp_path / "sales_cache.db").replace(os.sep, "/")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with patch.object(state_helpers, "_get_sync_db_session", side_effect=lambda: Session(engine)) as factory:
        yield engine, factory
    engine.dispose()


NOW = _dt.datetime.utcnow().replace(hour=6, minute=0, second=0, microsecond=0)


def _add_orders(engine, store_id, orders):
    from backend.db.models import SalesOrderDB
    with Session(engine) as s:
        for i, (days_ago, price, payload) in enumerate(orders):
            s.add(SalesOrderDB(
                store_id=store_id, order_internal_id=f"{store_id}-{i}", order_code=f"c{i}",
                created_at=NOW - _dt.timedelta(days=days_ago), total_price=price,
                status="COMPLETED", state="ARCHIVE",
                entries_payload=payload if isinstance(payload, str) else json.dumps(payload),
            ))
        s.commit()


def _kaspi_order(internal_id, days_ago, sku, qty, price):
    created = NOW - _dt.timedelta(days=days_ago)
    return {
        "internalId": internal_id, "code": internal_id, "totalPrice": price,
        "status": "COMPLETED", "state": "ARCHIVE",
        "creationDateMs": int(created.replace(tzinfo=_dt.timezone.utc).timestamp() * 1000),
        "entries": [{"sku": sku, "name": sku, "quantity": qty, "totalPrice": price}],
    }


def test_repeated_reads_hit_db_once(db):
    engine, factory = db
    from backend.services import sales_orders_cache
    _add_orders(engine, "s1", [(1, 100.0, [{"sku": "A", "quantity": 1}]), (3, 50.0, [])])

    since = NOW - _dt.timedelta(days=10)
    first = sales_orders_cache.orders_since("s1", since)
    second = sales_orders_cache.orders_since("s1", since)
    assert first == second
    assert [r.total_price for r in first] == [50.0, 100.0]  # по возрастанию created_at
    assert factory.call_count == 1


def test_narrower_window_served_from_cache_wider_reloads(db):
    engine, factory = db
    from backend.services import sales_orders_cache
    _add_orders(engine, "s1", [(1, 1.0, []), (5, 5.0, []), (20, 20.0, [])])

    assert len(sales_orders_cache.orders_since("s1", NOW - _dt.timedelta(days=10))) == 2
    assert [r.total_price for r in sales_orders_cache.orders_since("s1", NOW - _dt.timedelta(days=2))] == [1.0]
    assert factory.call_count == 1
    assert len(sales_orders_cache.orders_since("s1", NOW - _dt.timedelta(days=30))) == 3
    assert factory.call_count == 2


def test_ttl_expiry_reloads(db):
    engine, factory = db
    from backend.services import sales_orders_cache
    _add_orders(engine, "s1", [(1, 1.0, [])])
    since = NOW - _dt.timedelta(days=10)
    with patch.object(sales_orders_cache.time, "monotonic", return_value=1000) as clock:
        sales_orders_cache.orders_since("s1", since)
        clock.return_value = 1000 + sales_orders_cache._TTL_SECONDS
        sales_orders_cache.orders_since("s1", since)
    assert factory.call_count == 2


def test_upsert_invalidates_forecast_and_summary(db):
    engine, _ = db
    from backend import state_helpers
    from backend.services import stock_forecast

    state_helpers._upsert_sales_orders([_kaspi_order("o1", 2, "A", 1, 100)], "s1")
    today = (NOW + _dt.timedelta(hours=5)).date()
    since = today - _dt.timedelta(days=30)
    with patch.object(state_helpers, "_load_products", return_value=[]):
        assert sum(stock_forecast.load_daily_sales("s1", since)["A"].values()) == 1
        assert state_helpers._load_sales_summary("s1", days=30)["total_orders"] == 1

        # Новый заказ и правка состава старого — оба видны сразу.
        changed = _kaspi_order("o1", 2, "A", 3, 300)
        state_helpers._upsert_sales_orders([changed, _kaspi_order("o2", 1, "B", 2, 40)], "s1")

        daily = stock_forecast.load_daily_sales("s1", since)
        summary = state_helpers._load_sales_summary("s1", days=30)
    assert sum(daily["A"].values()) == 3
    assert sum(daily["B"].values()) == 2
    assert summary["total_orders"] == 2
    assert {s["sku"]: s["quantity"] for s in summary["top_skus"]} == {"A": 3, "B": 2}


def test_bad_payload_counted_as_order_but_not_as_sales(db):
    engine, _ = db
    from backend import state_helpers
    from backend.services import stock_forecast
    _add_orders(engine, "s1", [
        (1, 100.0, "{not json"),
        (1, 70.0, [{"sku": " A ", "name": "Товар", "quantity": 2, "totalPrice": 70}]),
        (1, 0.0, [{"sku": "", "quantity": 5}, {"sku": "Z", "quantity": 0}]),
    ])
    today = (NOW + _dt.timedelta(hours=5)).date()
    daily = stock_forecast.load_daily_sales("s1", today - _dt.timedelta(days=30))
    assert {k: sum(v.values()) for k, v in daily.items()} == {"A": 2}

    with patch.object(state_helpers, "_load_products", return_value=[]):
        summary = state_helpers._load_sales_summary("s1", days=30)
    assert summary["total_orders"] == 3
    assert summary["total_revenue"] == 170.0
    by_sku = {s["sku"]: s for s in summary["top_skus"]}
    assert by_sku[" A "]["quantity"] == 2   # сводка, как и раньше, артикул не нормализует
    assert by_sku["?"]["quantity"] == 5     # пустой артикул — «?»


def test_chat_activity_grouped_by_utc_day(db):
    engine, _ = db
    from backend import state_helpers
    from backend.db.models import ChatResponseLogDB
    day1 = NOW.replace(hour=23, minute=50) - _dt.timedelta(days=2)
    with Session(engine) as s:
        for created in (day1, day1 + _dt.timedelta(minutes=5), day1 + _dt.timedelta(minutes=20),
                        NOW - _dt.timedelta(days=400)):
            s.add(ChatResponseLogDB(store_id="s1", group_id="g", created_at=created))
        s.add(ChatResponseLogDB(store_id="other", group_id="g", created_at=day1))
        s.commit()

    activity = state_helpers._load_chat_activity("s1", days=180)
    next_day = day1 + _dt.timedelta(minutes=20)
    assert activity == [
        {"date": day1.strftime("%Y-%m-%d"), "count": 2},
        {"date": next_day.strftime("%Y-%m-%d"), "count": 1},
    ]


def test_master_groups_without_sync_logs_do_not_wipe_them(db):
    engine, _ = db
    from backend import state_helpers
    from backend.db.models import MasterStockGroupDB
    with Session(engine) as s:
        s.add(MasterStockGroupDB(group_id="g1", store_id="s1", group_name="Г", shared_stock=3,
                                 linked_skus=json.dumps(["A"]), sync_logs=json.dumps(["лог 1", "лог 2"])))
        s.commit()

    full = state_helpers._load_master_stock_groups("s1")
    light = state_helpers._load_master_stock_groups("s1", include_sync_logs=False)
    assert full["g1"]["sync_logs"] == ["лог 1", "лог 2"]
    assert "sync_logs" not in light["g1"]
    assert {k: v for k, v in full["g1"].items() if k != "sync_logs"} == light["g1"]

    light["g1"]["shared_stock"] = 7
    state_helpers._save_master_stock_groups(light, "s1")
    again = state_helpers._load_master_stock_groups("s1")
    assert again["g1"]["shared_stock"] == 7
    assert again["g1"]["sync_logs"] == ["лог 1", "лог 2"]


def test_competitor_leaderboard_cached_per_store_with_ttl():
    from backend.routers import analytics
    analytics._leaderboard_cache.clear()
    calls = []

    def fake(store_id, days, limit):
        calls.append(store_id)
        return [{"name": f"rival-{store_id}", "count": len(calls)}]

    try:
        with patch.object(analytics, "_load_competitor_leaderboard", side_effect=fake), \
             patch.object(analytics.time, "monotonic", return_value=1000) as clock:
            first = analytics._get_competitor_leaderboard_cached("s1")
            assert analytics._get_competitor_leaderboard_cached("s1") is first
            analytics._get_competitor_leaderboard_cached("s2")
            assert calls == ["s1", "s2"]
            clock.return_value = 1000 + analytics._LEADERBOARD_CACHE_TTL_SECONDS
            assert analytics._get_competitor_leaderboard_cached("s1")[0]["count"] == 3
    finally:
        analytics._leaderboard_cache.clear()


def test_derived_is_built_once_per_cache_version(db):
    engine, _ = db
    from backend.services import sales_orders_cache
    _add_orders(engine, "s1", [(1, 1.0, []), (5, 5.0, [])])
    since = NOW - _dt.timedelta(days=10)
    builds = []

    def build(rows):
        builds.append(len(rows))
        return sum(r.total_price for r in rows)

    assert sales_orders_cache.derived("s1", since, "total", build) == 6.0
    assert sales_orders_cache.derived("s1", since, "total", build) == 6.0
    assert sales_orders_cache.derived("s1", NOW - _dt.timedelta(days=2), "total", build) == 1.0
    assert builds == [2, 1]

    sales_orders_cache.bump("s1")  # синк записал заказы — пересчёт
    sales_orders_cache.derived("s1", since, "total", build)
    assert builds == [2, 1, 2]
