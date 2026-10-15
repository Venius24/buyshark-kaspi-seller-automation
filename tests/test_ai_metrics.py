import asyncio
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from backend.services import ai_metrics as metrics
from scripts.ai_baseline import CHAT_SQL, RUN_SQL, collect_baseline, render


ROOT = Path(__file__).resolve().parents[1]


def sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0


def test_assistant_metrics_and_bounded_labels():
    row = SimpleNamespace(error=None, truncated=False, tool_errors=0, duration_ms=1500,
                          prompt_tokens=12, completion_tokens=4, cost_usd=.03,
                          steps=json.dumps([{"kind": "tool", "name": "get_orders", "ok": True},
                                            {"kind": "tool", "name": "private-store-id", "ok": False}]))
    before = sample("assistant_runs_total", {"outcome": "ok"})
    before_tool = sample("assistant_tool_calls_total", {"tool": "other", "ok": "no"})
    before_cost = sample("assistant_cost_usd_total")
    metrics.record_assistant_run(None, row)
    assert sample("assistant_runs_total", {"outcome": "ok"}) == before + 1
    assert sample("assistant_tool_calls_total", {"tool": "other", "ok": "no"}) == before_tool + 1
    assert sample("assistant_cost_usd_total") == pytest.approx(before_cost + .03)
    assert sample("assistant_tokens_total", {"type": "prompt"}) >= 12
    assert sample("assistant_run_duration_seconds_count") >= 1
    before_error = sample("assistant_runs_total", {"outcome": "error"})
    row.tool_errors = 1
    metrics.record_assistant_run(None, row)
    assert sample("assistant_runs_total", {"outcome": "error"}) == before_error + 1
    for family in REGISTRY.collect():
        if family.name.startswith(("assistant_", "chatbot_")):
            assert all("store_id" not in s.labels for s in family.samples)
            assert all("private-store-id" not in s.labels.values() for s in family.samples)


def test_chatbot_metrics_and_failure_isolation(monkeypatch):
    before_other = sample("chatbot_replies_total", {"kind": "other"})
    before_prompt = sample("chatbot_tokens_total", {"type": "prompt"})
    metrics.record_chatbot_reply("unknown-kind")
    metrics.record_chatbot_reply("inbound_reply", {"prompt_tokens": 8, "completion_tokens": 3})
    metrics.observe_chatbot_ai_latency(.8)
    metrics.record_chatbot_ai_reply(SimpleNamespace(products=[{"card": {"id": 1}}]))
    metrics.record_chatbot_ai_reply(SimpleNamespace(products=[{"stock": 1}]))
    assert sample("chatbot_replies_total", {"kind": "other"}) == before_other + 1
    assert sample("chatbot_tokens_total", {"type": "prompt"}) == before_prompt + 8
    assert sample("chatbot_ai_replies_total", {"card_facts": "yes"}) >= 1
    assert sample("chatbot_ai_replies_total", {"card_facts": "no"}) >= 1
    assert sample("chatbot_ai_latency_seconds_count") >= 1

    def fail(*args, **kwargs):
        raise RuntimeError("metric failure")

    monkeypatch.setattr(metrics.chatbot_ai_latency_seconds, "observe", fail)
    metrics.observe_chatbot_ai_latency(1)
    metrics.record_chatbot_reply("inbound_reply", {"prompt_tokens": object()})


def test_baseline_read_only_and_aggregates(tmp_path):
    query_text = str(CHAT_SQL) + str(RUN_SQL)
    for forbidden in ("client_text", "reply_text", "order_code", "detail"):
        assert forbidden not in query_text
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP)\b", query_text, re.I)

    db_path = tmp_path / "ai.sqlite"
    now = datetime(2026, 10, 16, 12)
    connection = sqlite3.connect(db_path)
    connection.executescript("""
        CREATE TABLE chat_response_logs (created_at TIMESTAMP, kind TEXT, prompt_tokens INTEGER,
                                         completion_tokens INTEGER, client_text TEXT, reply_text TEXT,
                                         order_code TEXT, detail TEXT);
        CREATE TABLE assistant_runs (started_at TIMESTAMP, duration_ms REAL, prompt_tokens INTEGER,
                                     completion_tokens INTEGER, error TEXT, tool_errors INTEGER);
    """)
    connection.executemany("INSERT INTO chat_response_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        (now - timedelta(days=1), "inbound_reply", 10, 4, "secret", "secret", "secret", "secret"),
        (now - timedelta(days=1), "inbound_reply", 30, 6, "secret", "secret", "secret", "secret"),
        (now - timedelta(days=1), "inbound_escalated_ai", None, None, "secret", "secret", "secret", "secret"),
        (now - timedelta(days=1), "outbound_review", None, None, "secret", "secret", "secret", "secret"),
        (now - timedelta(days=30), "inbound_reply", 500, 500, "secret", "secret", "secret", "secret"),
    ])
    connection.executemany("INSERT INTO assistant_runs VALUES (?, ?, ?, ?, ?, ?)", [
        (now - timedelta(days=1), 1000, 10, 5, None, 0),
        (now - timedelta(days=2), 3000, 20, 7, None, 1),
        (now - timedelta(days=30), 9000, 50, 30, "old", 0),
    ])
    connection.commit()
    connection.close()

    result = asyncio.run(collect_baseline(f"sqlite+aiosqlite:///{db_path.as_posix()}", days=14, now=now))
    day = result["chatbot_by_day"][0]
    assert day["replies"] == 3
    assert day["kinds"] == {"inbound_escalated_ai": 1, "inbound_reply": 2}
    assert day["escalation_rate"] == pytest.approx(1 / 3)
    assert day["avg_prompt_tokens_per_inbound_reply"] == 20
    assert day["avg_completion_tokens_per_inbound_reply"] == 5
    assert result["assistant"] == {"runs": 2, "p50_duration_ms": 2000,
                                    "p95_duration_ms": 2900, "prompt_tokens": 30,
                                    "completion_tokens": 12, "error_rate": .5}
    output = render(result)
    assert "```json" in output
    assert "secret" not in output


def test_dashboard_uses_defined_metrics_and_unique_uid():
    folder = ROOT / "deploy/monitoring/provisioning/dashboards/json"
    dashboards = [json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("*.json")]
    assert len({dashboard["uid"] for dashboard in dashboards}) == len(dashboards)
    dashboard = next(item for item in dashboards if item["uid"] == "buyshark-ai")
    expressions = [target["expr"] for panel in dashboard["panels"] for target in panel["targets"]]
    names = set(re.findall(r"\b(?:assistant|chatbot)_[a-z_]+(?:_total|_bucket)\b", " ".join(expressions)))
    expected = {"assistant_runs_total", "assistant_run_duration_seconds_bucket", "assistant_tokens_total",
                "assistant_cost_usd_total", "assistant_tool_calls_total", "chatbot_replies_total",
                "chatbot_tokens_total", "chatbot_ai_latency_seconds_bucket", "chatbot_ai_replies_total"}
    assert names == expected
    registered = {sample.name for family in REGISTRY.collect() for sample in family.samples}
    assert names <= registered
    assert len(dashboard["panels"]) >= 9
