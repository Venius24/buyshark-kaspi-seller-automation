"""Недельный отчёт по защите демпинга (просьба владельца 07.10.2026).

Три вопроса:
1. Как защита повлияла на количество продаж.
2. Как повлияла на деньги.
3. Что с конкурентами — сейчас и по сравнению с началом недели.

Запуск на сервере (из /root/buyshark, с загруженным .env):
    python -m backend.services.guard_weekly_report baseline   # снимок «до», в начале недели
    python -m backend.services.guard_weekly_report report     # отчёт за прошедшую неделю

Продажи берутся из Kaspi API по дате создания заказа во всех состояниях, а не
из sales_orders: там только завершённые заказы, и последние 4–5 дней всегда
неполные. Основная мера — созданные заказы (включая потом отменённые): отмены
у свежих заказов ещё не успели случиться, и «чистые» цифры недели были бы
нечестно лучше прошлых недель. Чистые (без отмен) — рядом, для сведения.

Влияние отделяем от общего фона рынка: сравниваем изменение у товаров, где
работала защита, с изменением у товаров без демпинга (контрольная группа).
"""
import asyncio
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional

from sqlalchemy import text

STORE_ID = "3cce2de7-6c6e-4345-9b22-dc664214e9e6"
LOCAL = dt.timedelta(hours=5)                      # Казахстан, UTC+5
OUT_DIR = os.environ.get("GUARD_REPORT_DIR", "/root/guard_reports")
PRIOR_WEEKS = 3
STATES = ("NEW", "SIGN_REQUIRED", "PICKUP", "DELIVERY", "KASPI_DELIVERY", "ARCHIVE")
NIGHT_HOURS = range(3, 7)                          # ночь из настроек Бархана с 07.10


def _is_cancelled(status: str) -> bool:
    s = (status or "").upper()
    return "CANCEL" in s or "RETURN" in s


def _token(store_id: str) -> str:
    from backend.security.store_context import list_active_stores
    for s in asyncio.run(list_active_stores()):
        if s.store_id == store_id:
            return s.token
    raise SystemExit(f"Нет активного токена для магазина {store_id}")


def fetch_orders(token: str, ge_utc: dt.datetime, le_utc: dt.datetime) -> List[dict]:
    """Все заказы, созданные в [ge, le), с составом.

    Фильтр Kaspi `creationDate` на деле срабатывает по более поздней дате
    заказа (замер 07.10.2026): заказы, созданные 03.10, находятся только окнами
    за 05–06.10, а окно, кончающееся 04.10, отдало за 03.10 11 заказов вместо
    197. Поэтому сканируем окнами по суткам от ge до текущего момента и сами
    отбираем по настоящей дате создания."""
    from backend.kaspi_wrapper import KaspiAPI

    out, seen = [], set()
    start = ge_utc
    scan_until = dt.datetime.utcnow()
    while start < scan_until:
        end = min(start + dt.timedelta(days=1), scan_until)
        ge_ms, le_ms = int(start.replace(tzinfo=dt.timezone.utc).timestamp() * 1000), \
            int(end.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) - 1
        for state in STATES:
            def fetch_page(n, state=state, ge_ms=ge_ms, le_ms=le_ms):
                return KaspiAPI._get(f"{KaspiAPI.BASE_URL}/orders", token, {
                    "page[number]": n, "page[size]": 100, "include[orders]": "entries",
                    "filter[orders][creationDate][$ge]": ge_ms,
                    "filter[orders][creationDate][$le]": le_ms,
                    "filter[orders][state]": state,
                })
            for _n, resp in KaspiAPI._fetch_pages_batched(fetch_page):
                if resp.status_code != 200:
                    break
                js = resp.json()
                data = js.get("data", [])
                if not data:
                    break
                inc = {i.get("id"): i for i in js.get("included", []) if i.get("type") == "orderentries"}
                for o in data:
                    a = o.get("attributes", {})
                    created = dt.datetime.utcfromtimestamp(a.get("creationDate", 0) / 1000)
                    if o.get("id") in seen or not (ge_utc <= created < le_utc):
                        continue
                    seen.add(o.get("id"))
                    entries = []
                    for ref in o.get("relationships", {}).get("entries", {}).get("data", []):
                        e = inc.get(ref.get("id"))
                        if not e:
                            continue
                        ea = e.get("attributes", {})
                        entries.append({
                            "sku": str((ea.get("offer") or {}).get("code", "")).strip(),
                            "qty": ea.get("quantity", 1) or 1,
                            "total": float(ea.get("totalPrice") or ea.get("entryTotalPrice") or 0),
                        })
                    out.append({
                        "created": created,
                        "status": a.get("status", ""),
                        "total": float(a.get("totalPrice") or 0),
                        "entries": entries,
                    })
                if len(data) < 100:
                    break
        start = end
    return out


def _db():
    from backend.state_helpers import _get_sync_db_session
    return _get_sync_db_session()


def sku_groups(store_id: str, ge: dt.datetime, le: dt.datetime) -> Dict[str, set]:
    """cooled — было охлаждение рынка; guarded — защита хоть раз держала/двигала
    цену; dumping — товары под демпингом (есть в раскладе войны); остальное —
    контрольная группа."""
    db = _db()
    try:
        moves = db.execute(text(
            "select sku, action from price_war_moves where store_id=:s and created_at>=:a and created_at<:b"),
            {"s": store_id, "a": ge, "b": le}).fetchall()
        dumping = {r.sku for r in db.execute(text(
            "select sku from price_war_snapshots where store_id=:s"), {"s": store_id}).fetchall()}
    finally:
        db.close()
    cooled = {m.sku for m in moves if m.action == "GUARD_LADDER_RESET"}
    guarded = {m.sku for m in moves if (m.action or "").startswith("GUARD_")}
    return {"cooled": cooled, "guarded": guarded | cooled, "dumping": dumping | {m.sku for m in moves}}


def _bucket(orders: List[dict], ge: dt.datetime, le: dt.datetime, skus: Optional[set], exclude: Optional[set] = None):
    gross_n = net_n = 0
    gross_rev = net_rev = 0.0
    units = 0
    night_n = 0
    for o in orders:
        if not (ge <= o["created"] < le):
            continue
        ent = [e for e in o["entries"]
               if (skus is None or e["sku"] in skus) and not (exclude and e["sku"] in exclude)]
        if not ent:
            continue
        rev = sum(e["total"] for e in ent)
        gross_n += 1
        gross_rev += rev
        units += sum(e["qty"] for e in ent)
        if (o["created"] + LOCAL).hour in NIGHT_HOURS:
            night_n += 1
        if not _is_cancelled(o["status"]):
            net_n += 1
            net_rev += rev
    return {"orders": gross_n, "revenue": round(gross_rev), "units": units,
            "avg_unit_price": round(gross_rev / units) if units else None,
            "orders_net": net_n, "revenue_net": round(net_rev), "night_orders": night_n}


def _week_bounds(report_week_start_local: dt.date):
    start_utc = dt.datetime.combine(report_week_start_local, dt.time()) - LOCAL
    return start_utc, start_utc + dt.timedelta(days=7)


def _change(cur: float, base: float) -> Optional[float]:
    return round((cur / base - 1) * 100, 1) if base else None


def competitors(store_id: str) -> dict:
    """Текущий расклад по товарам под демпингом: наше место, лучший соперник,
    топ-5 соперников с отзывами (пишется циклом демпинга с 07.10.2026)."""
    db = _db()
    try:
        rows = db.execute(text(
            "select sku, name, our_price, our_rank, competitors_count, best_competitor_price, "
            "best_competitor_name, guard_json, updated_at from price_war_snapshots where store_id=:s"),
            {"s": store_id}).fetchall()
    finally:
        db.close()
    items, merchants = [], {}
    for r in rows:
        g = json.loads(r.guard_json) if r.guard_json else {}
        rivals = g.get("rivals") or []
        items.append({"sku": r.sku, "name": r.name, "our_price": r.our_price, "our_rank": r.our_rank,
                      "competitors": r.competitors_count, "best_price": r.best_competitor_price,
                      "best_name": r.best_competitor_name, "rivals": rivals})
        for rv in rivals:
            mid = rv.get("merchant_id")
            if not mid:
                continue
            m = merchants.setdefault(mid, {"name": rv.get("name"), "reviews": None, "rating": None, "skus": 0})
            m["skus"] += 1
            if rv.get("reviews") is not None:
                m["reviews"] = max(m["reviews"] or 0, rv["reviews"])
            if rv.get("rating") is not None:
                m["rating"] = rv["rating"]
    ranks = [i["our_rank"] for i in items if i["our_rank"]]
    return {
        "taken_at": dt.datetime.utcnow().isoformat(),
        "skus": len(items),
        "first_place": sum(1 for r in ranks if r == 1),
        "second_place": sum(1 for r in ranks if r == 2),
        "avg_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
        "items": items,
        "merchants": merchants,
    }


def encounters(store_id: str, ge: dt.datetime, le: dt.datetime, top: int = 10) -> List[dict]:
    db = _db()
    try:
        rows = db.execute(text(
            "select competitor_name, count(*) n, count(distinct sku) skus from competitor_encounter_logs "
            "where store_id=:s and created_at>=:a and created_at<:b group by 1 order by 2 desc limit :t"),
            {"s": store_id, "a": ge, "b": le, "t": top}).fetchall()
    finally:
        db.close()
    return [{"name": r.competitor_name, "cycles": r.n, "skus": r.skus} for r in rows]


def guard_events(store_id: str, ge: dt.datetime, le: dt.datetime) -> dict:
    db = _db()
    try:
        rows = db.execute(text(
            "select action, count(*) n, count(distinct sku) skus from price_war_moves "
            "where store_id=:s and created_at>=:a and created_at<:b group by 1"),
            {"s": store_id, "a": ge, "b": le}).fetchall()
        followed = db.execute(text("""
            with r as (select sku, created_at t, our_new from price_war_moves
                       where store_id=:s and created_at>=:a and created_at<:b and action='GUARD_LADDER_RESET')
            select r.sku, r.our_new,
              (select m.rival_price from price_war_moves m where m.store_id=:s and m.sku=r.sku
                 and m.created_at>r.t order by m.created_at limit 1) next_rival
            from r"""), {"s": store_id, "a": ge, "b": le}).fetchall()
    finally:
        db.close()
    f = [x for x in followed if x.next_rival is not None]
    return {
        "actions": {r.action: {"moves": r.n, "skus": r.skus} for r in rows},
        "coolings": len(followed),
        # Соперник «пошёл следом», если следующий наш ход был ответом на его цену
        # не ниже 95% цены охлаждения.
        "coolings_followed": sum(1 for x in f if x.next_rival >= 0.95 * x.our_new),
    }


def baseline(store_id: str = STORE_ID) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    snap = competitors(store_id)
    path = os.path.join(OUT_DIR, f"baseline_{(dt.datetime.utcnow() + LOCAL).date().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=1, default=str)
    return path


def _latest_baseline(before: dt.datetime) -> Optional[dict]:
    if not os.path.isdir(OUT_DIR):
        return None
    files = sorted(f for f in os.listdir(OUT_DIR) if f.startswith("baseline_"))
    best = None
    for f in files:
        d = dt.date.fromisoformat(f[len("baseline_"):-len(".json")])
        if dt.datetime.combine(d, dt.time()) - LOCAL <= before + dt.timedelta(days=1):
            best = f
    if not best:
        return None
    with open(os.path.join(OUT_DIR, best), encoding="utf-8") as fh:
        return json.load(fh)


def build(store_id: str = STORE_ID, week_start_local: Optional[dt.date] = None) -> dict:
    today = (dt.datetime.utcnow() + LOCAL).date()
    if week_start_local is None:   # последняя полная неделя пн–вс
        week_start_local = today - dt.timedelta(days=today.weekday() + 7)
    ge, le = _week_bounds(week_start_local)
    prior_ge = ge - dt.timedelta(days=7 * PRIOR_WEEKS)
    token = _token(store_id)
    orders = fetch_orders(token, prior_ge, le)
    groups = sku_groups(store_id, ge, le)

    def side(skus, exclude=None):
        cur = _bucket(orders, ge, le, skus, exclude)
        weeks = [_bucket(orders, ge - dt.timedelta(days=7 * (i + 1)), ge - dt.timedelta(days=7 * i), skus, exclude)
                 for i in range(PRIOR_WEEKS)]
        base = {k: (sum(w[k] or 0 for w in weeks) / PRIOR_WEEKS) for k in ("orders", "revenue", "units",
                                                                           "orders_net", "revenue_net",
                                                                           "night_orders")}
        base["avg_unit_price"] = round(base["revenue"] / base["units"]) if base["units"] else None
        return {"week": cur, "prior_avg": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in base.items()},
                "prior_weeks": weeks,
                "change_pct": {k: _change(cur[k] or 0, base[k] or 0)
                               for k in ("orders", "revenue", "units", "avg_unit_price", "night_orders")}}

    sales = {
        "all": side(None),
        "cooled": side(groups["cooled"]),
        "guarded": side(groups["guarded"]),
        "dumping": side(groups["dumping"]),
        "control": side(None, exclude=groups["dumping"]),
    }
    ctrl = sales["control"]["change_pct"]
    for k in ("cooled", "guarded", "dumping"):
        sales[k]["vs_control_pp"] = {m: (round(sales[k]["change_pct"][m] - ctrl[m], 1)
                                         if sales[k]["change_pct"][m] is not None and ctrl[m] is not None else None)
                                     for m in ("orders", "revenue", "avg_unit_price")}

    now_comp = competitors(store_id)
    base_comp = _latest_baseline(ge)
    rivals_delta = []
    if base_comp:
        for mid, m in now_comp["merchants"].items():
            b = (base_comp.get("merchants") or {}).get(mid)
            if b and m.get("reviews") is not None and b.get("reviews") is not None:
                rivals_delta.append({"name": m["name"], "merchant_id": mid, "reviews_then": b["reviews"],
                                     "reviews_now": m["reviews"], "new_reviews": m["reviews"] - b["reviews"],
                                     "skus": m["skus"]})
        rivals_delta.sort(key=lambda x: -x["new_reviews"])

    return {
        "store_id": store_id,
        "week_local": [week_start_local.isoformat(), (week_start_local + dt.timedelta(days=6)).isoformat()],
        "generated_at": dt.datetime.utcnow().isoformat(),
        "groups_size": {k: len(v) for k, v in groups.items()},
        "sales": sales,
        "guard": guard_events(store_id, ge, le),
        "competitors_now": {k: v for k, v in now_comp.items() if k not in ("items", "merchants")},
        "competitors_baseline": ({k: v for k, v in base_comp.items() if k not in ("items", "merchants")}
                                 if base_comp else None),
        "rivals_new_reviews": rivals_delta[:15],
        "who_beats_us_week": encounters(store_id, ge, le),
        "who_beats_us_prev_week": encounters(store_id, ge - dt.timedelta(days=7), ge),
    }


def main(argv: List[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else "report"
    if cmd == "baseline":
        print(baseline())
        return
    week = dt.date.fromisoformat(argv[2]) if len(argv) > 2 else None
    rep = build(week_start_local=week)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"report_{rep['week_local'][0]}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1, default=str)
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv)
