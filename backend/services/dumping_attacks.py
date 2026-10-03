"""
Отчёт «Под атакой» для вкладки демпинга (2026-10-03, HANDOFF §3.111).

Источники — только то, что уже пишет цикл демпинга, без единого похода в Kaspi:
- price_war_snapshots.guard_json — сигналы атаки по товару на последнем цикле
  (провокатор вплотную, лесенка, ферма, тень — см. brain.BrainWorker);
- price_war_moves — история наших ходов: из неё «сколько ₸ с единицы съела
  война» = пик нашей цены за 30 дней минус текущая цена.
"""

from typing import Dict, List

SIGNAL_LABELS = {
    "weak_rival": "Провокатор вплотную",
    "ladder": "Лесенка",
    "farm": "Несколько провокаторов",
    "shadow": "Ходит за нами по товарам",
}


def would_differs(would, current=None) -> bool:
    """В режиме наблюдения защита поставила бы другую цену, чем обычный
    демпинг (brain кладёт would только при расхождении)."""
    return bool(would and would.get("price") is not None)


def summarize(snapshots: List[dict], moves_by_sku: Dict[str, List[dict]]) -> dict:
    items = []
    rivals: Dict[str, dict] = {}
    total_lost = 0.0

    for snap in snapshots:
        g = snap.get("guard") or {}
        signals = list(g.get("signals") or [])
        moves = moves_by_sku.get(snap["sku"], [])
        # Прыжок на максимум при сбросе лесенки — не «потерянные» деньги.
        resets = [m for m in moves if m.get("action") == "GUARD_LADDER_RESET"]
        peak = max([m["our_old"] if m.get("action") == "GUARD_LADDER_RESET" else max(m["our_old"], m["our_new"])
                    for m in moves], default=None)
        current = snap.get("our_price") or 0.0
        lost = round(peak - current, 2) if peak and peak > current else 0.0
        downs = sum(1 for m in moves if m["our_new"] < m["our_old"])
        would = g.get("would")
        if not signals and not lost and not would_differs(would, current) and not g.get("asks"):
            continue
        if signals:
            total_lost += lost
        items.append({
            "sku": snap["sku"],
            "name": snap.get("name"),
            "url": snap.get("url"),
            "our_price": current,
            "our_rank": snap.get("our_rank"),
            "peak_price_30d": peak,
            "lost_per_unit": lost,
            "lost_pct": round(lost / peak * 100, 1) if peak else 0.0,
            "down_moves_30d": downs,
            "signals": signals,
            "suspects": g.get("suspects") or [],
            "ladder": g.get("ladder"),
            "ladder_active": g.get("ladder_active", False),
            "ignored": g.get("ignored") or [],
            "guard_limit": g.get("limit"),
            "guard_enabled": g.get("enabled", False),
            "observe": g.get("observe", False),
            "would": would if would_differs(would, current) else None,
            "asks": g.get("asks") or 0,
            "pause_until": g.get("pause_until"),
            "last_reset_at": g.get("last_reset_at"),
            "ladder_resets_30d": len(resets),
            "best_competitor_name": snap.get("best_competitor_name"),
            "best_competitor_price": snap.get("best_competitor_price"),
            "checked_at": snap.get("updated_at"),
        })

        def bump(mid, name, kind, extra=None):
            if not mid:
                return
            r = rivals.setdefault(mid, {"merchant_id": mid, "name": name, "skus": set(), "kinds": set()})
            r["skus"].add(snap["sku"])
            r["kinds"].add(kind)
            if extra:
                r.update({k: v for k, v in extra.items() if v is not None})

        for sp in g.get("suspects") or []:
            if sp.get("adjacent"):
                bump(sp.get("merchant_id"), sp.get("name"), "weak_rival",
                     {"reviews": sp.get("reviews"), "rating": sp.get("rating"), "age_days": sp.get("age_days")})
        if g.get("ladder"):
            bump(g["ladder"].get("rival_id"), g["ladder"].get("rival_name"), "ladder")
        for sh in g.get("shadow") or []:
            bump(sh.get("merchant_id"), sh.get("name"), "shadow")

    # Сначала товары с сигналами, внутри — по съеденным деньгам.
    items.sort(key=lambda it: (not it["signals"], -it["lost_per_unit"]))
    rival_list = sorted(
        (
            {**r, "skus": sorted(r["skus"]), "kinds": sorted(r["kinds"]), "sku_count": len(r["skus"])}
            for r in rivals.values()
        ),
        key=lambda r: (-r["sku_count"], r["name"] or ""),
    )
    under_attack = [it for it in items if it["signals"]]
    return {
        "items": items,
        "rivals": rival_list[:50],
        "summary": {
            "under_attack": len(under_attack),
            "ladders": sum(1 for it in under_attack if "ladder" in it["signals"]),
            "weak_rivals": sum(1 for it in under_attack if "weak_rival" in it["signals"]),
            "lost_per_unit_total": round(total_lost, 2),
            # В режиме наблюдения защита ничего не делала — там счётчик would_change.
            "guarded": sum(1 for it in items if not it["observe"] and (it["guard_limit"] or it["ignored"])),
            "would_change": sum(1 for it in items if it["would"]),
            "ladder_resets_30d": sum(it["ladder_resets_30d"] for it in items),
            "paused": sum(1 for it in items if it["pause_until"]),
        },
        "signal_labels": SIGNAL_LABELS,
    }


def build_report(store_id: str) -> dict:
    from backend.state_helpers import _load_price_war_snapshot, _load_price_war_moves, _list_guard_decisions
    snapshots = _load_price_war_snapshot(store_id)
    moves = _load_price_war_moves(store_id, hours=30 * 24)
    report = summarize(snapshots, moves)
    pending = _list_guard_decisions(store_id, pending_only=True)
    report["pending"] = pending
    report["summary"]["pending_asks"] = len(pending)
    return report
