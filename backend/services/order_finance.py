"""
Финансы заказов Kaspi: выручка, комиссия, деньги на доставку, возвраты, отмены.

Откуда какие цифры (сверено на живых заказах Бархана 2026-10-03):

- Доставка — ТОЧНАЯ. `attributes.deliveryCostForSeller` — то, что Kaspi
  списывает с продавца, уже с НДС: заказ на 1 190 ₸ → 173 ₸ = 149 ₸ по
  тарифной сетке 2026 × 1,16. `deliveryCost` — это доплата ПОКУПАТЕЛЯ, её не
  трогаем.
- Возвраты/отмены — ТОЧНЫЕ. Фильтр `state=ARCHIVE` + `status` (RETURNED /
  CANCELLED) Kaspi честно соблюдает только для ARCHIVE; у отмены есть
  `cancellationReason`.
- Комиссия — ОЦЕНКА. В Shop API её нет вообще. Считаем по официальной
  таблице тарифов (kaspi_commission_2026.json, категория 5-го уровня ==
  `orderentries.category.title`). Расходится с кабинетом в акциях
  «рассрочка 0-0-24» (там ставка своя) и если Kaspi поменяет таблицу.
  Точную цифру по заказу даёт только раздел начислений кабинета — не
  исследован.

Когорта — по дате СОЗДАНИЯ заказа (единственный фильтр дат у Kaspi, окно
≤14 дней за запрос). Заказы последних дней ещё в пути (не ARCHIVE) и в
отчёт не попадают — это не потеря, а «ещё не закрылись».
"""

import json
import logging
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

VAT_RATE = 0.16
FINANCE_STATUSES = ("COMPLETED", "RETURNED", "CANCELLED")
_WINDOW_DAYS = 13
_RATES_PATH = os.path.join(os.path.dirname(__file__), "kaspi_commission_2026.json")

# Причины отмены, встреченные на живых заказах Бархана (2026-10-03):
# код → (подпись, чья вина). Kaspi их не документирует; неизвестный код
# показываем как есть, а не прячем в «прочее».
CANCELLATION_REASONS = {
    "BUYER_CANCELLATION_HIMSELF": ("Покупатель отменил сам", "buyer"),
    "TIMEOUT_BUYER_PICKUP": ("Покупатель не забрал заказ вовремя", "buyer"),
    "BUYER_CANCELLATION_BY_COURIER": ("Покупатель отказался при курьере", "buyer"),
    "TIMEOUT_BUYER_SIGNATURE": ("Покупатель не подписал вовремя", "buyer"),
    "MERCHANT_OUT_OF_STOCK": ("Нет в наличии", "merchant"),
    "RFO_REJECTED": ("Отклонено банком", "kaspi"),
    "TECHNICAL_PROBLEM_CANCELLATION": ("Техническая отмена Kaspi", "kaspi"),
    "TECHNICAL_CANCELLATION_NO_PUSH": ("Техническая отмена Kaspi", "kaspi"),
    "GLOBAL_TIMEOUT": ("Истёк общий срок заказа", "kaspi"),
    "TIMEOUT_KASPI_DELIVERY": ("Просрочка Kaspi Доставки", "kaspi"),
}


@lru_cache(maxsize=1)
def load_commission_table() -> dict:
    with open(_RATES_PATH, encoding="utf-8") as f:
        return json.load(f)


def commission_rate(category_title: Optional[str]) -> tuple[float, float, bool]:
    """(% без НДС, % с НДС, найдено_в_таблице). Неизвестная категория —
    дефолт 10,9/12,5: на нём 94% строк официальной таблицы."""
    table = load_commission_table()
    leaf = table["leaves"].get((category_title or "").strip())
    if leaf:
        return leaf[1], leaf[2], True
    no_vat, with_vat = table["default"]
    return no_vat, with_vat, False


def parse_orders_response(res_json: dict) -> list[dict]:
    """Сырой ответ /orders?include[orders]=entries → плоские заказы с позициями."""
    included = {
        item.get("id"): item.get("attributes", {})
        for item in res_json.get("included", [])
        if item.get("type") == "orderentries"
    }
    orders = []
    for o in res_json.get("data", []):
        a = o.get("attributes", {})
        kd = a.get("kaspiDelivery") or {}
        entries = []
        for ref in o.get("relationships", {}).get("entries", {}).get("data", []):
            e = included.get(ref.get("id"))
            if e is None:
                continue
            offer = e.get("offer") or {}
            category = e.get("category") or {}
            entries.append({
                "sku": str(offer.get("code") or "").strip(),
                "name": offer.get("name"),
                "qty": e.get("quantity") or 0,
                "total_price": float(e.get("totalPrice") or 0),
                "category": category.get("title"),
            })
        orders.append({
            "id": o.get("id"),
            "code": a.get("code"),
            "status": a.get("status"),
            "state": a.get("state"),
            "created_ms": a.get("creationDate"),
            "total_price": float(a.get("totalPrice") or 0),
            "delivery_cost_for_seller": float(a.get("deliveryCostForSeller") or 0),
            "payment_mode": a.get("paymentMode"),
            "credit_term": a.get("creditTerm"),
            "cancellation_reason": a.get("cancellationReason"),
            "returned_to_warehouse": bool(kd.get("returnedToWarehouse")),
            "entries": entries,
        })
    return orders


def fetch_finance_orders(token: str, ge_ms: int, le_ms: int) -> list[dict]:
    """Все закрытые заказы (COMPLETED/RETURNED/CANCELLED) в окнах ≤13 дней."""
    from backend.kaspi_wrapper import KaspiAPI

    window_ms = _WINDOW_DAYS * 86400 * 1000
    orders: list[dict] = []
    cursor = ge_ms
    while cursor < le_ms:
        w_end = min(cursor + window_ms, le_ms)
        for status in FINANCE_STATUSES:
            def fetch_page(page_number: int, _s=status, _ge=cursor, _le=w_end):
                return KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, {
                    "page[number]": page_number,
                    "page[size]": 100,
                    "include[orders]": "entries",
                    "filter[orders][creationDate][$ge]": _ge,
                    "filter[orders][creationDate][$le]": _le,
                    "filter[orders][state]": "ARCHIVE",
                    "filter[orders][status]": _s,
                })

            for _page, resp in KaspiAPI._fetch_pages_batched(fetch_page):
                if resp.status_code != 200:
                    raise RuntimeError(f"Kaspi /orders {status}: HTTP {resp.status_code}")
                page_orders = parse_orders_response(resp.json())
                orders.extend(page_orders)
                if len(page_orders) < 100:
                    break
        cursor = w_end
    # Фильтр Kaspi `creationDate` для ARCHIVE на деле срабатывает по дате
    # закрытия заказа (completionDate, замер 08.10.2026): окно [ge, le] отдаёт
    # заказы, закрытые в нём, в том числе созданные до ge. Окна и так тянутся
    # до текущего момента, поэтому созданные в периоде заказы найдены все,
    # а лишние (созданные раньше) отбрасываем здесь: отчёт по когорте создания.
    # Окна стыкуются по границе ($ge/$le включительно) — дедуп по id.
    return list({o["id"]: o for o in orders if (o["created_ms"] or 0) >= ge_ms}.values())


def _order_commission(order: dict) -> tuple[float, float, list[str]]:
    """(комиссия без НДС, с НДС, категории не из таблицы) по позициям заказа.
    Без позиций (Kaspi не отдал include) — дефолтная ставка от суммы заказа."""
    no_vat = with_vat = 0.0
    unknown = []
    entries = order.get("entries") or []
    if not entries:
        r_no, r_with, _ = commission_rate(None)
        return order["total_price"] * r_no / 100, order["total_price"] * r_with / 100, []
    for e in entries:
        r_no, r_with, found = commission_rate(e.get("category"))
        no_vat += e["total_price"] * r_no / 100
        with_vat += e["total_price"] * r_with / 100
        if not found and e.get("category"):
            unknown.append(e["category"])
    return no_vat, with_vat, unknown


def summarize_finance(orders: Iterable[dict]) -> dict:
    completed, returned, cancelled = [], [], []
    for o in orders:
        {"COMPLETED": completed, "RETURNED": returned, "CANCELLED": cancelled}.get(o["status"], []).append(o)

    revenue = sum(o["total_price"] for o in completed)

    comm_no_vat = comm_with_vat = 0.0
    by_category: dict[str, dict] = defaultdict(lambda: {"revenue": 0.0, "commission": 0.0})
    unknown_categories: Counter = Counter()
    for o in completed:
        n, w, unknown = _order_commission(o)
        comm_no_vat += n
        comm_with_vat += w
        unknown_categories.update(unknown)
        for e in o.get("entries") or []:
            _, r_with, _ = commission_rate(e.get("category"))
            row = by_category[e.get("category") or "—"]
            row["revenue"] += e["total_price"]
            row["commission"] += e["total_price"] * r_with / 100

    delivery_completed = sum(o["delivery_cost_for_seller"] for o in completed)
    delivery_returned = sum(o["delivery_cost_for_seller"] for o in returned)

    returned_skus: Counter = Counter()
    for o in returned:
        for e in o.get("entries") or []:
            returned_skus[e["sku"]] += e["qty"]

    reasons = Counter(o.get("cancellation_reason") or "UNKNOWN" for o in cancelled)
    payment_modes = Counter(
        (o.get("payment_mode") or "UNKNOWN") + (f"_{o['credit_term']}" if o.get("credit_term") else "")
        for o in completed
    )

    closed_sales = len(completed) + len(returned)
    net = revenue - comm_with_vat - delivery_completed - delivery_returned

    def r(x: float) -> float:
        return round(x, 2)

    return {
        "orders": {"completed": len(completed), "returned": len(returned), "cancelled": len(cancelled)},
        "revenue": r(revenue),
        "commission": {
            "total": r(comm_with_vat),
            "without_vat": r(comm_no_vat),
            "vat": r(comm_with_vat - comm_no_vat),
            "share_of_revenue_pct": r(comm_with_vat / revenue * 100) if revenue else 0.0,
            "is_estimate": True,
            # В акции «рассрочка 0-0-24» Kaspi берёт свою ставку, не табличную.
            "uncertain_orders": sum(
                1 for o in completed
                if o.get("payment_mode") == "PAY_WITH_CREDIT" and (o.get("credit_term") or 0) >= 24
            ),
            "unknown_categories": dict(unknown_categories.most_common(20)),
            "by_category": sorted(
                ({"category": k, "revenue": r(v["revenue"]), "commission": r(v["commission"])}
                 for k, v in by_category.items()),
                key=lambda x: -x["commission"],
            )[:20],
        },
        "delivery": {
            "completed": r(delivery_completed),
            "returned": r(delivery_returned),
            "total": r(delivery_completed + delivery_returned),
            "avg_per_order": r(delivery_completed / len(completed)) if completed else 0.0,
        },
        "returns": {
            "count": len(returned),
            "amount": r(sum(o["total_price"] for o in returned)),
            "rate_pct": r(len(returned) / closed_sales * 100) if closed_sales else 0.0,
            "returned_to_warehouse": sum(1 for o in returned if o["returned_to_warehouse"]),
            "top_skus": [{"sku": s, "qty": q} for s, q in returned_skus.most_common(10)],
        },
        "cancellations": {
            "count": len(cancelled),
            "amount": r(sum(o["total_price"] for o in cancelled)),
            "merchant_fault": sum(v for k, v in reasons.items()
                                  if CANCELLATION_REASONS.get(k, ("", ""))[1] == "merchant"),
            "by_reason": [
                {"reason": k, "label": CANCELLATION_REASONS.get(k, (k, ""))[0],
                 "fault": CANCELLATION_REASONS.get(k, ("", "unknown"))[1], "count": v}
                for k, v in reasons.most_common()
            ],
        },
        "payment_modes": dict(payment_modes.most_common()),
        # Выручка минус то, что удержит Kaspi. Себестоимость товара, упаковка
        # и налоги продавца сюда не входят — их у нас нет.
        "net_after_kaspi": r(net),
    }


def compute_store_finance(token: str, days: int, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    start = now - timedelta(days=days)
    ge_ms, le_ms = int(start.timestamp() * 1000), int(now.timestamp() * 1000)
    t0 = time.monotonic()
    orders = fetch_finance_orders(token, ge_ms, le_ms)
    summary = summarize_finance(orders)
    logger.info(f"[OrderFinance] {len(orders)} заказов за {days} дн. за {time.monotonic() - t0:.1f}с")
    return {
        "period": {"from": start.isoformat() + "Z", "to": now.isoformat() + "Z", "days": days,
                   "cohort": "creation_date"},
        "commission_table": load_commission_table()["effective_from"],
        **summary,
    }
