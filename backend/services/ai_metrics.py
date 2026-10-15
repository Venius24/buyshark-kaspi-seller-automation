"""Low-cardinality Prometheus metrics for the two AI products."""

import json
import logging
import math

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

ASSISTANT_TOOLS = frozenset({
    "get_store_overview", "get_orders", "search_products", "get_sales_stats",
    "get_dumping_status", "set_dumping_enabled", "propose_price",
    "search_store_knowledge", "build_report",
})
CHATBOT_KINDS = frozenset({
    "inbound_reply", "inbound_escalated_ai", "inbound_escalated_trigger",
    "inbound_logistics_template", "inbound_skipped_no_credits", "inbound_send_failed",
})

assistant_runs_total = Counter("assistant_runs_total", "Assistant runs", ["outcome"])
assistant_run_duration_seconds = Histogram(
    "assistant_run_duration_seconds", "Assistant run duration in seconds",
    buckets=(.5, 1, 2, 3, 5, 10, 15, 30, 45, 60),
)
assistant_tokens_total = Counter("assistant_tokens_total", "Assistant tokens", ["type"])
assistant_tool_calls_total = Counter("assistant_tool_calls_total", "Assistant tool calls", ["tool", "ok"])
assistant_cost_usd_total = Counter("assistant_cost_usd_total", "Known assistant cost in USD")

chatbot_replies_total = Counter("chatbot_replies_total", "Chatbot journal outcomes", ["kind"])
chatbot_tokens_total = Counter("chatbot_tokens_total", "Tokens for sent AI chatbot replies", ["type"])
chatbot_ai_latency_seconds = Histogram(
    "chatbot_ai_latency_seconds", "Chatbot AI generation duration in seconds",
    buckets=(.5, 1, 2, 3, 5, 10, 15, 30, 45, 60),
)
chatbot_ai_replies_total = Counter("chatbot_ai_replies_total", "Completed chatbot AI generations", ["card_facts"])


def _nonnegative(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("metric value must be finite and nonnegative")
    return number


def record_assistant_run(turn, run_row):
    """Record one persisted run; failures in instrumentation are isolated."""
    try:
        outcome = "error" if run_row.error or run_row.tool_errors else ("truncated" if run_row.truncated else "ok")
        assistant_runs_total.labels(outcome=outcome).inc()
        assistant_run_duration_seconds.observe(_nonnegative(run_row.duration_ms) / 1000)
        assistant_tokens_total.labels(type="prompt").inc(_nonnegative(run_row.prompt_tokens))
        assistant_tokens_total.labels(type="completion").inc(_nonnegative(run_row.completion_tokens))
        if run_row.cost_usd is not None:
            assistant_cost_usd_total.inc(_nonnegative(run_row.cost_usd))
        for step in json.loads(run_row.steps):
            if step.get("kind") == "tool":
                name = step.get("name")
                assistant_tool_calls_total.labels(
                    tool=name if isinstance(name, str) and name in ASSISTANT_TOOLS else "other",
                    ok="yes" if step.get("ok") else "no",
                ).inc()
    except Exception:
        logger.exception("Failed to record assistant metrics")


def record_chatbot_reply(kind, usage=None):
    """Record a journaled outcome; token rate counts successful sent AI replies."""
    try:
        chatbot_replies_total.labels(kind=kind if isinstance(kind, str) and kind in CHATBOT_KINDS else "other").inc()
        if kind == "inbound_reply" and usage:
            for token_type in ("prompt", "completion"):
                value = usage.get(f"{token_type}_tokens")
                if value is not None:
                    chatbot_tokens_total.labels(type=token_type).inc(_nonnegative(value))
    except Exception:
        logger.exception("Failed to record chatbot reply metrics")


def observe_chatbot_ai_latency(seconds):
    try:
        chatbot_ai_latency_seconds.observe(_nonnegative(seconds))
    except Exception:
        logger.exception("Failed to record chatbot AI latency")


def record_chatbot_ai_reply(facts):
    try:
        products = getattr(facts, "products", None) or []
        has_card = any(isinstance(product, dict) and "card" in product for product in products)
        chatbot_ai_replies_total.labels(card_facts="yes" if has_card else "no").inc()
    except Exception:
        logger.exception("Failed to record chatbot AI facts")
