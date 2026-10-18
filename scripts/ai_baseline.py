"""Read-only historical AI baseline from DATABASE_URL."""

import argparse
import asyncio
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


CHAT_SQL = text("""
SELECT DATE(created_at) AS day, kind, COUNT(*) AS replies,
       SUM(prompt_tokens) AS prompt_tokens, COUNT(prompt_tokens) AS prompt_count,
       SUM(completion_tokens) AS completion_tokens, COUNT(completion_tokens) AS completion_count
FROM chat_response_logs
WHERE created_at >= :since
  AND kind IN ('inbound_reply', 'inbound_escalated_ai', 'inbound_escalated_trigger',
               'inbound_logistics_template', 'inbound_skipped_no_credits', 'inbound_send_failed')
GROUP BY DATE(created_at), kind
ORDER BY day, kind
""")
RUN_SQL = text("""
SELECT DATE(started_at) AS day, duration_ms, prompt_tokens, completion_tokens,
       CASE WHEN error IS NOT NULL OR tool_errors > 0 THEN 1 ELSE 0 END AS is_error
FROM assistant_runs
WHERE started_at >= :since
ORDER BY started_at
""")


def _async_url(raw):
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (index - lower)


async def collect_baseline(database_url, days=14, now=None):
    """Only SELECT aggregate and numeric fields; never reads message contents."""
    if days < 1:
        raise ValueError("days must be positive")
    since = (now or datetime.utcnow()) - timedelta(days=days)
    engine = create_async_engine(_async_url(database_url))
    try:
        async with engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                # Must be the first statement of the transaction.
                await conn.execute(text("SET TRANSACTION READ ONLY"))
            chat_rows = (await conn.execute(CHAT_SQL, {"since": since})).mappings().all()
            # Migration 051 may not be applied yet; a missing table means no runs.
            has_runs = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("assistant_runs"))
            run_rows = (await conn.execute(RUN_SQL, {"since": since})).mappings().all() if has_runs else []
    finally:
        await engine.dispose()

    chat_days = defaultdict(lambda: {"kinds": {}, "replies": 0, "escalations": 0,
                                    "reply_prompt_tokens": 0, "reply_prompt_count": 0,
                                    "reply_completion_tokens": 0, "reply_completion_count": 0})
    for row in chat_rows:
        day = chat_days[str(row["day"])]
        kind = row["kind"]
        count = int(row["replies"])
        day["kinds"][kind] = count
        day["replies"] += count
        if kind in ("inbound_escalated_ai", "inbound_escalated_trigger"):
            day["escalations"] += count
        if kind == "inbound_reply":
            for token_type in ("prompt", "completion"):
                day[f"reply_{token_type}_tokens"] += int(row[f"{token_type}_tokens"] or 0)
                day[f"reply_{token_type}_count"] += int(row[f"{token_type}_count"])

    chat = []
    for date, data in sorted(chat_days.items()):
        data["day"] = date
        data["escalation_rate"] = data["escalations"] / data["replies"] if data["replies"] else 0
        for token_type in ("prompt", "completion"):
            count = data[f"reply_{token_type}_count"]
            data[f"avg_{token_type}_tokens_per_inbound_reply"] = data[f"reply_{token_type}_tokens"] / count if count else None
            del data[f"reply_{token_type}_tokens"]
            del data[f"reply_{token_type}_count"]
        chat.append(data)

    durations = [float(row["duration_ms"]) for row in run_rows]
    runs = {"runs": len(run_rows), "p50_duration_ms": _percentile(durations, .5),
            "p95_duration_ms": _percentile(durations, .95),
            "prompt_tokens": sum(int(row["prompt_tokens"] or 0) for row in run_rows),
            "completion_tokens": sum(int(row["completion_tokens"] or 0) for row in run_rows),
            "error_rate": sum(int(row["is_error"]) for row in run_rows) / len(run_rows) if run_rows else 0}
    return {"days": days, "since_utc": since.isoformat(), "chatbot_by_day": chat, "assistant": runs}


def render(result):
    lines = ["| День UTC | Kind | Ответы | Доля эскалаций за день | Prompt / inbound_reply | Completion / inbound_reply |",
             "|---|---|---:|---:|---:|---:|"]
    for day in result["chatbot_by_day"]:
        for kind, count in sorted(day["kinds"].items()):
            prompt = day["avg_prompt_tokens_per_inbound_reply"] if kind == "inbound_reply" else None
            completion = day["avg_completion_tokens_per_inbound_reply"] if kind == "inbound_reply" else None
            prompt_text = f"{prompt:.2f}" if prompt is not None else "—"
            completion_text = f"{completion:.2f}" if completion is not None else "—"
            lines.append(f"| {day['day']} | {kind} | {count} | {day['escalation_rate']:.2%} | "
                         f"{prompt_text} | {completion_text} |")
    run = result["assistant"]
    lines += ["", "| Запуски | p50, мс | p95, мс | Prompt | Completion | Доля ошибок |",
              "|---:|---:|---:|---:|---:|---:|",
              f"| {run['runs']} | {run['p50_duration_ms']} | {run['p95_duration_ms']} | "
              f"{run['prompt_tokens']} | {run['completion_tokens']} | {run['error_rate']:.2%} |",
              "", "```json", json.dumps(result, ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be set")
    print(render(asyncio.run(collect_baseline(database_url, args.days))))


if __name__ == "__main__":
    main()
