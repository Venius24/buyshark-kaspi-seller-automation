"""
Финансы заказов (backend/services/order_finance.py): разбор сырого ответа
Kaspi /orders, оценка комиссии по официальной таблице тарифов 2026, доставка
из deliveryCostForSeller, возвраты/отмены. Формы ответов взяты с живых
заказов Бархана 2026-10-03.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

from backend.services import order_finance as of


def _raw_order(oid, status, total, dcfs, entries, **extra):
    attrs = {
        "code": oid, "status": status, "state": "ARCHIVE", "creationDate": 1789569091039,
        "totalPrice": total, "deliveryCostForSeller": dcfs, "deliveryCost": 500.0,
        "paymentMode": "PREPAID", "isKaspiDelivery": True,
        "kaspiDelivery": {"returnedToWarehouse": False},
        **extra,
    }
    return {
        "id": oid, "type": "orders", "attributes": attrs,
        "relationships": {"entries": {"data": [{"id": f"{oid}#{i}", "type": "orderentries"} for i in range(len(entries))]}},
    }


def _raw_entries(oid, entries):
    return [
        {"id": f"{oid}#{i}", "type": "orderentries", "attributes": {
            "quantity": qty, "totalPrice": price, "basePrice": price,
            "category": {"code": "Master - x", "title": cat},
            "offer": {"code": sku, "name": None},
        }}
        for i, (sku, cat, qty, price) in enumerate(entries)
    ]


def _response(*orders):
    data, included = [], []
    for oid, status, total, dcfs, entries, extra in orders:
        data.append(_raw_order(oid, status, total, dcfs, entries, **extra))
        included += _raw_entries(oid, entries)
    return {"data": data, "included": included}


def test_commission_rate_uses_official_table():
    assert of.commission_rate("Воздушные шары") == (10.9, 12.5, True)
    no_vat, with_vat, found = of.commission_rate("Несуществующая категория")
    assert (no_vat, with_vat, found) == (10.9, 12.5, False)
    # В таблице есть все три ставки 2026 года.
    rates = {tuple(v[1:]) for v in of.load_commission_table()["leaves"].values()}
    assert rates == {(10.9, 12.5), (6.4, 7.3), (13.5, 15.5)}


def test_parse_takes_seller_delivery_cost_not_buyer():
    orders = of.parse_orders_response(_response(
        ("A", "COMPLETED", 1190.0, 173.0, [("S517-0019", "Велосипедные замки", 1, 1190.0)], {}),
    ))
    assert orders[0]["delivery_cost_for_seller"] == 173.0
    assert orders[0]["entries"] == [{
        "sku": "S517-0019", "name": None, "qty": 1, "total_price": 1190.0, "category": "Велосипедные замки",
    }]


def test_summary_counts_commission_delivery_returns_cancellations():
    food = next(k for k, v in of.load_commission_table()["leaves"].items() if v[1] == 6.4)
    orders = of.parse_orders_response(_response(
        ("A", "COMPLETED", 1000.0, 173.0, [("S1", "Воздушные шары", 1, 1000.0)], {}),
        ("B", "COMPLETED", 3000.0, 231.0, [("S2", food, 2, 2000.0), ("S3", "Нет в таблице", 1, 1000.0)],
         {"paymentMode": "PAY_WITH_CREDIT", "creditTerm": 24}),
        ("C", "RETURNED", 1488.0, 173.0, [("S1", "Воздушные шары", 1, 1488.0)], {}),
        ("D", "CANCELLED", 1290.0, 0.0, [("S4", "Воздушные шары", 1, 1290.0)],
         {"cancellationReason": "MERCHANT_OUT_OF_STOCK"}),
        ("E", "CANCELLED", 500.0, 0.0, [("S4", "Воздушные шары", 1, 500.0)],
         {"cancellationReason": "SOMETHING_NEW"}),
    ))
    s = of.summarize_finance(orders)

    assert s["orders"] == {"completed": 2, "returned": 1, "cancelled": 2}
    assert s["revenue"] == 4000.0
    # 1000×12,5% + 2000×7,3% + 1000×12,5% (дефолт для неизвестной категории)
    assert s["commission"]["total"] == 125.0 + 146.0 + 125.0
    assert s["commission"]["without_vat"] == 109.0 + 128.0 + 109.0
    assert s["commission"]["unknown_categories"] == {"Нет в таблице": 1}
    assert s["commission"]["uncertain_orders"] == 1  # рассрочка 0-0-24

    assert s["delivery"] == {"completed": 404.0, "returned": 173.0, "total": 577.0, "avg_per_order": 202.0}
    assert s["returns"]["count"] == 1 and s["returns"]["amount"] == 1488.0
    assert s["returns"]["rate_pct"] == round(1 / 3 * 100, 2)

    assert s["cancellations"]["merchant_fault"] == 1
    reasons = {r["reason"]: r for r in s["cancellations"]["by_reason"]}
    assert reasons["MERCHANT_OUT_OF_STOCK"]["fault"] == "merchant"
    # Неизвестный код не прячем — показываем как есть.
    assert reasons["SOMETHING_NEW"]["label"] == "SOMETHING_NEW"
    assert s["net_after_kaspi"] == 4000.0 - 396.0 - 577.0


def test_fetch_queries_each_status_and_dedups_window_borders():
    page = _response(("A", "COMPLETED", 1000.0, 173.0, [("S1", "Воздушные шары", 1, 1000.0)], {}))
    resp = MagicMock(status_code=200)
    resp.json.return_value = page
    with patch("backend.kaspi_wrapper.KaspiAPI._get", return_value=resp) as get:
        day = 86400 * 1000
        orders = of.fetch_finance_orders("tok", 0, 20 * day)  # два окна по ≤13 дней

    statuses = [c.args[2]["filter[orders][status]"] for c in get.call_args_list]
    assert sorted(set(statuses)) == sorted(of.FINANCE_STATUSES)
    assert len(get.call_args_list) == 2 * len(of.FINANCE_STATUSES)
    assert all(c.args[2]["filter[orders][state]"] == "ARCHIVE" for c in get.call_args_list)
    assert [o["id"] for o in orders] == ["A"]


def test_fetch_raises_on_kaspi_error():
    with patch("backend.kaspi_wrapper.KaspiAPI._get", return_value=MagicMock(status_code=401)):
        try:
            of.fetch_finance_orders("tok", 0, 1000)
        except RuntimeError as e:
            assert "401" in str(e)
        else:
            raise AssertionError("ожидали RuntimeError")


def test_finance_endpoint_caches_per_store_and_clamps_days():
    from types import SimpleNamespace
    from backend.routers import analytics

    analytics._finance_cache.clear()
    ctx = SimpleNamespace(token="tok", store_id="store-a")
    with patch("backend.routers.analytics.compute_store_finance", return_value={"revenue": 1.0}) as calc:
        first = analytics.get_order_finance(days=999, ctx=ctx)
        second = analytics.get_order_finance(days=999, ctx=ctx)

    calc.assert_called_once_with("tok", analytics._FINANCE_MAX_DAYS)
    assert first["cached"] is False and second["cached"] is True
    assert second["revenue"] == 1.0
