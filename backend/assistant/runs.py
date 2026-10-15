"""Store-scoped run persistence and bounded summary statistics."""
import json
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import defer

from backend.db.models import AssistantRunDB as Run

SAMPLE_LIMIT = 5000


async def save_run(db, *, store_id, conversation_id, user_id, started_at, turn, cfg):
    prompt = turn.usage.get("prompt_tokens") or 0
    completion = turn.usage.get("completion_tokens") or 0
    cost = None
    if cfg.price_prompt_per_1m is not None and cfg.price_completion_per_1m is not None:
        cost = (prompt * cfg.price_prompt_per_1m + completion * cfg.price_completion_per_1m) / 1_000_000
    # Explicit projection is defense in depth against future engine additions.
    steps = []
    for step in turn.steps:
        clean = {key: step[key] for key in ("kind", "name", "duration_ms", "ok")}
        if step.get("error"):
            clean["error"] = str(step["error"])[:200]
        steps.append(clean)
    error = turn.error or next((s.get("error") for s in steps if s["kind"] == "llm" and not s["ok"]), None)
    row = Run(store_id=store_id, conversation_id=conversation_id, user_id=user_id,
              started_at=started_at, duration_ms=turn.duration_ms,
              model=turn.usage.get("model") or cfg.model, provider=turn.provider,
              prompt_tokens=prompt, completion_tokens=completion, cost_usd=cost,
              llm_calls=turn.llm_calls, tool_calls=sum(s["kind"] == "tool" for s in steps),
              tool_errors=sum(s["kind"] == "tool" and not s["ok"] for s in steps),
              truncated=turn.truncated, error=str(error)[:500] if error else None,
              steps=json.dumps(steps, ensure_ascii=False))
    db.add(row)
    await db.flush()
    return row


def run_out(row, *, with_steps=False):
    result = {column.name: getattr(row, column.name) for column in Run.__table__.columns if column.name != "steps"}
    result["started_at"] = row.started_at.isoformat()
    if with_steps:
        result["steps"] = json.loads(row.steps)
    return result


async def list_runs(db, store_id, limit):
    rows = (await db.execute(select(Run).options(defer(Run.steps)).where(Run.store_id == store_id)
                            .order_by(Run.started_at.desc(), Run.id.desc()).limit(limit))).scalars()
    return [run_out(row) for row in rows]


async def get_run(db, store_id, run_id):
    row = (await db.execute(select(Run).where(Run.store_id == store_id, Run.id == run_id))).scalar_one_or_none()
    return run_out(row, with_steps=True) if row else None


def percentile(values, fraction):
    """Linear interpolation at (n-1)*fraction (same as SQL percentile_cont)."""
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lower = int(index)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (index - lower)


async def summary(db, store_id, days):
    filters = (Run.store_id == store_id, Run.started_at >= datetime.utcnow() - timedelta(days=days))
    total, prompt, completion, cost, priced, errors, truncated = (await db.execute(select(
        func.count(Run.id), func.coalesce(func.sum(Run.prompt_tokens), 0),
        func.coalesce(func.sum(Run.completion_tokens), 0), func.sum(Run.cost_usd),
        func.count(Run.cost_usd),
        func.sum(case((or_(Run.error.is_not(None), Run.tool_errors > 0), 1), else_=0)),
        func.sum(case((Run.truncated.is_(True), 1), else_=0)),
    ).where(*filters))).one()
    rows = (await db.execute(select(Run.duration_ms, Run.steps).where(*filters)
                            .order_by(Run.started_at.desc(), Run.id.desc()).limit(SAMPLE_LIMIT))).all()
    calls, failures = Counter(), Counter()
    for _, raw in rows:
        for step in json.loads(raw):
            if step["kind"] == "tool":
                calls[step["name"]] += 1
                if not step["ok"]:
                    failures[step["name"]] += 1
    durations = [row.duration_ms for row in rows]
    return {"days": days, "runs": total, "p50_duration_ms": percentile(durations, .5),
            "p95_duration_ms": percentile(durations, .95), "prompt_tokens": prompt,
            "completion_tokens": completion, "cost_usd": cost, "priced_runs": priced,
            "error_rate": errors / total if total else 0,
            "truncated_rate": truncated / total if total else 0,
            "tool_errors": dict(failures),
            "top_tools": [{"name": name, "calls": count} for name, count in sorted(calls.items(), key=lambda pair: (-pair[1], pair[0]))],
            "sample": {"limit": SAMPLE_LIMIT, "rows": len(rows), "truncated": total > len(rows),
                       "order": "latest_started_at", "fields": ["p50_duration_ms", "p95_duration_ms", "tool_errors", "top_tools"]}}
