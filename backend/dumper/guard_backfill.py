"""
Разовый перенос истории ходов демпинга из dumper_logs в price_war_moves.

Зачем: защита от лесенки (guard.detect_ladder) и отчёт «Под атакой» читают
price_war_moves, а она начинает копиться только с выкатки. Старые строки
Executor'а в dumper_logs содержат нашу старую/новую цену и цену соперника —
этого хватает, чтобы лесенка и «сколько съела война» были видны сразу.
Имени соперника в старых логах нет: такие ходы пишутся с пустым rival_id
(detect_ladder считает их одним соперником).

    python -m backend.dumper.guard_backfill --store <store_id> --days 14          # посчитать
    python -m backend.dumper.guard_backfill --store <store_id> --days 14 --apply  # записать

Повторный запуск безопасен: ходы, уже лежащие в price_war_moves за тот же
период, не дублируются (переносится только то, что старше самого раннего
настоящего хода).
"""

import argparse
import json
import re
from datetime import datetime, timedelta

_RX = re.compile(
    r"Авто-демпинг (\S+) \(город \d+\): (\d+) ₸ -> (\d+) ₸ \(конкурент: (\d+) ₸, действие: ([A-Z0-9_]+)"
)


def parse_log_message(message: str):
    m = _RX.search(message or "")
    if not m:
        return None
    return {
        "sku": m[1],
        "our_old": float(m[2]),
        "our_new": float(m[3]),
        "rival_price": float(m[4]),
        "action": m[5],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from sqlalchemy import select, func
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import DumperLogDB, PriceWarMoveDB

    session = _get_sync_db_session()
    try:
        since = datetime.utcnow() - timedelta(days=args.days)
        earliest_real = session.scalar(
            select(func.min(PriceWarMoveDB.created_at)).where(
                PriceWarMoveDB.store_id == args.store, PriceWarMoveDB.rival_id.is_not(None)
            )
        )
        already = session.scalar(
            select(func.count()).select_from(PriceWarMoveDB).where(
                PriceWarMoveDB.store_id == args.store, PriceWarMoveDB.rival_id.is_(None)
            )
        )
        if already:
            print(f"Перенос уже делался ({already} ходов без соперника) — выходим.")
            return
        stmt = select(DumperLogDB.created_at, DumperLogDB.payload).where(
            DumperLogDB.store_id == args.store, DumperLogDB.created_at >= since
        )
        if earliest_real:
            stmt = stmt.where(DumperLogDB.created_at < earliest_real)
        rows = session.execute(stmt.order_by(DumperLogDB.id)).all()
        moves = []
        for created_at, payload in rows:
            try:
                msg = json.loads(payload).get("message", "")
            except Exception:
                continue
            parsed = parse_log_message(msg)
            if parsed:
                moves.append((created_at, parsed))
        print(f"Строк лога: {len(rows)}, ходов распознано: {len(moves)}, SKU: {len({m[1]['sku'] for m in moves})}")
        if not args.apply:
            print("Пробный прогон — ничего не записано (добавьте --apply).")
            return
        for created_at, mv in moves:
            session.add(PriceWarMoveDB(
                store_id=args.store,
                sku=mv["sku"][:100],
                created_at=created_at,
                our_old=mv["our_old"],
                our_new=mv["our_new"],
                rival_price=mv["rival_price"],
                action=mv["action"],
            ))
        session.commit()
        print(f"Записано ходов: {len(moves)}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
