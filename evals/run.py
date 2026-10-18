"""python -m evals.run [--live]. Never loads .env or connects to application DB."""
import argparse
import asyncio
import json
import logging
import os
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import socket
import tempfile
from unittest.mock import Mock, patch

import httpx
import requests
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.assistant import builtin_tools, config, proposals, report_tool, tools
from backend.assistant.engine import run_turn
from backend.assistant.prompt import StoreBrief
from backend.assistant.provider import LLMProvider, LLMResult, ToolCall, get_provider
from backend.assistant.rag import index as rag_index
from backend.db.models import (Base, Store, User, StoreProductDB, DumpingConfigDB,
                               AssistantPriceProposalDB)
from backend import feature_flags, state_helpers
from backend.services import stock_forecast, order_finance


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, script):
        self.script = iter(script)

    async def complete(self, messages, tools=None):
        item = next(self.script)  # Exhaustion is a scenario error, never a silent success.
        return LLMResult(text=item.get("text", ""), error=item.get("error"),
                         tool_calls=[ToolCall(id=f"call-{i}", name=call["name"], arguments=call.get("arguments", {}))
                                     for i, call in enumerate(item.get("tool_calls", []))])


@dataclass
class EvalResult:
    name: str
    passed: bool
    detail: str = ""


def load_cases():
    return [json.loads(line) for line in Path(__file__).with_name("cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected external IO or price approval in eval")


async def evaluate(case, live=False):
    # All IO guards are scoped and restored even when a case fails.
    with tempfile.TemporaryDirectory(prefix="assistant_eval_") as directory, ExitStack() as stack:
        stack.enter_context(patch.object(config, "load_env", lambda: None))
        stack.enter_context(patch.dict(os.environ, {
            "ASSISTANT_ALLOW_WRITE_TOOLS": "1" if case.get("allow_write") else "0",
            "ASSISTANT_MAX_PENDING_PROPOSALS": "50", "ASSISTANT_PRICE_GATEWAY": "dry_run"}))
        stack.enter_context(patch.object(requests.sessions.Session, "request", forbidden))
        if not live:
            stack.enter_context(patch.object(socket.socket, "connect", forbidden))
            stack.enter_context(patch.object(socket, "create_connection", forbidden))
            stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", forbidden))
            stack.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", forbidden))
        gateway_guard = Mock(side_effect=forbidden)
        approve_guard = Mock(side_effect=forbidden)
        stack.enter_context(patch.object(proposals, "get_gateway", gateway_guard))
        stack.enter_context(patch.object(proposals, "approve_proposal", approve_guard))
        apply_guard = Mock(side_effect=forbidden)
        stack.enter_context(patch.object(proposals.DryRunGateway, "apply", apply_guard))
        today = datetime.now(timezone.utc).date().isoformat()
        daily = [{"date": today, "orders": 2, "revenue": 300}]
        summary = {"daily": daily, "total_orders": 2, "total_revenue": 300, "top_skus": []}
        observed_stores = []

        def loader(value):
            def load(store_id, *args, **kwargs):
                observed_stores.append(store_id)
                if store_id != "eval-store":
                    raise AssertionError("Foreign store reached a loader")
                return value
            return load

        products = [] if case.get("empty_search") else [{"merchantSku": "SKU", "name": "Fixture kettle",
                    "price_num": 150, "stock": 3, "dumping_state": "ON", "rank_state": "NO_DATA",
                    "dumping_config": {"min_price": 100, "max_price": 200}}]
        async def catalog(store_id, only_active=True):
            return loader(products)(store_id)
        stack.enter_context(patch.object(builtin_tools, "_annotated_catalog", catalog))
        stack.enter_context(patch.object(report_tool, "_annotated_catalog", catalog))
        for name, value in (("_load_sales_summary", summary), ("_load_sales_daily", daily),
                            ("_load_price_war_snapshot", []), ("_load_dumper_logs", []),
                            ("_load_products", products), ("_load_dumping_configs", {})):
            stack.enter_context(patch.object(state_helpers, name, loader(value)))
        stack.enter_context(patch.object(feature_flags, "get_flags", loader({"dumping_enabled": True})))
        stack.enter_context(patch.object(stock_forecast, "compute_stock_forecast", loader({"summary": {}, "items": []})))
        stack.enter_context(patch.object(order_finance, "compute_store_finance", lambda *args, **kwargs: {"revenue": 300, "net_after_kaspi": 250}))
        # The unavailable test exercises the real report exception boundary.
        if case.get("unavailable"):
            stack.enter_context(patch.object(state_helpers, "_load_sales_summary", forbidden))
        def drafts(store_id, status=None):
            observed_stores.append(store_id)
            if store_id != "eval-store":
                raise AssertionError("Foreign store reached a loader")
            rows = [{"id": "d1", "status": "approved", "draft_payload": {
                         "sku": "SKU", "category_name": "Чайники", "brand_name": "Fixture",
                         "features": {"color": "синий"}, "feature_meta": {"color": {"name": "Цвет"}},
                         "description": "Электрический чайник"}},
                    {"id": "d2", "status": "needs_review", "draft_payload": {
                         "sku": "UNREVIEWED", "features": {"color": "красный"},
                         "feature_meta": {"color": {"name": "Цвет"}}}}]
            return [row for row in rows if status is None or row["status"] == status]
        stack.enter_context(patch("backend.autolist.validator.list_drafts", drafts))
        stack.enter_context(patch("backend.assistant.card_facts.load_reviewed_cards", lambda store_id: []))
        stack.enter_context(patch.object(state_helpers, "_load_master_stock_groups", loader(case.get("groups", {}))))
        rag_index.clear_cache()
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(directory, 'eval.db').as_posix()}")
        @event.listens_for(engine.sync_engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add_all([Store(id=s, merchant_id=s, name=s) for s in ("eval-store", "foreign")])
                db.add(User(id="eval-user", email="eval@example.test", password_hash="unused"))
                await db.flush()
                db.add_all([StoreProductDB(store_id="eval-store", payload=json.dumps(products)),
                            DumpingConfigDB(store_id="eval-store", sku="SKU", min_price=100, max_price=200)])
                await db.flush()
                ctx = tools.ToolContext(store_id="eval-store", user_id="eval-user", role="OWNER",
                                        confirmed=True, db=db)
                cfg = config.AssistantConfig(enabled=True, provider="stub", model="eval", api_key="",
                    base_url="", max_tool_iterations=case.get("max_tool_iterations", 3), history_limit=10,
                    request_timeout=45, max_output_tokens=600, allow_write_tools=case.get("allow_write", False))
                if live:
                    from dataclasses import replace
                    cfg = replace(config.load_config(), allow_write_tools=cfg.allow_write_tools,
                                  max_tool_iterations=cfg.max_tool_iterations)
                    if cfg.is_stub:
                        raise ValueError("--live requires ASSISTANT_API_KEY and a real ASSISTANT_PROVIDER")
                script = json.loads(json.dumps(case["scenario"]).replace("$TODAY", today))
                turn = await run_turn(case["input"], [], StoreBrief(store_id=ctx.store_id), ctx, cfg,
                                      get_provider(cfg) if live else ScriptedProvider(script))
                trajectory = [t.name for t in turn.tool_trace]
                problems = []
                if turn.error:
                    problems.append(f"provider error: {turn.error}")
                if trajectory != case["expected_tools"]:
                    problems.append(f"tools expected={case['expected_tools']} actual={trajectory}")
                if not live:
                    if "tool_error" not in case["invariants"] and any(s["kind"] == "tool" and not s["ok"] for s in turn.steps):
                        problems.append("unexpected tool error")
                    known = {"tool_error", "no_numbers", "fallback", "unavailable", "nonempty_search", "sales_totals", "card_hit", "no_unreviewed_card"}
                    if set(case["invariants"]) - known:
                        problems.append("unknown invariant")
                    if any("approve" in spec.name or "gateway" in spec.name for spec in tools.all_specs()):
                        problems.append("approval/gateway exposed as tool")
                    for invariant in case["invariants"]:
                        results = [t.result for t in turn.tool_trace]
                        if invariant == "tool_error" and not any(r.get("error") or (r.get("result") or {}).get("error") for r in results):
                            problems.append("expected tool error")
                        elif invariant == "no_numbers":
                            allowed = set(re.findall(r"\d+(?:\.\d+)?", json.dumps(results)))
                            if set(re.findall(r"\d+(?:\.\d+)?", turn.reply)) - allowed:
                                problems.append("reply contains numbers absent from tool data")
                        elif invariant == "fallback" and not (turn.truncated and turn.reply.startswith("Не удалось собрать ответ:")):
                            problems.append("expected truncated fallback")
                        elif invariant == "unavailable" and not any(
                                (r.get("result", {}).get("sections", {}).get("sales", {}).get("status") == "unavailable") for r in results):
                            problems.append("report did not retain unavailable section")
                        elif invariant == "nonempty_search" and not results[0].get("result", {}).get("results"):
                            problems.append("fixture search returned no hits")
                        elif invariant == "card_hit" and not any(
                                h.get("source", {}).get("doc_type") == "card" and h.get("source", {}).get("sku") == "SKU"
                                for h in results[0].get("result", {}).get("results", [])):
                            problems.append("verified card was not found by characteristic")
                        elif invariant == "no_unreviewed_card" and any(
                                h.get("source", {}).get("sku") == "UNREVIEWED"
                                for r in results for h in r.get("result", {}).get("results", [])):
                            problems.append("unreviewed draft leaked into search")
                        elif invariant == "sales_totals" and results[0].get("result", {}).get("total_revenue") != 300:
                            problems.append("sales fixture total mismatch")
                    rows = list((await db.execute(select(AssistantPriceProposalDB))).scalars())
                    if len(rows) != case.get("proposals", 0) or any(r.status != "pending" or r.store_id != ctx.store_id for r in rows):
                        problems.append("unexpected proposal state")
                    if gateway_guard.called or approve_guard.called or apply_guard.called:
                        problems.append("approval/gateway called")
                    if any("approve" in name or "gateway" in name for name in trajectory):
                        problems.append("agent attempted approval/gateway")
                    if any(s != ctx.store_id for s in observed_stores):
                        problems.append("foreign loader access")
                    payload = (await db.execute(select(StoreProductDB.payload).where(StoreProductDB.store_id == ctx.store_id))).scalar_one()
                    if json.loads(payload) != products:
                        problems.append("catalog mutated")
                return EvalResult(case["id"], not problems, "; ".join(problems))
        finally:
            rag_index.clear_cache()
            await engine.dispose()


async def run_cases(live=False):
    results = []
    for case in load_cases():
        try:
            results.append(await evaluate(case, live))
        except Exception as exc:
            results.append(EvalResult(case["id"], False, f"{type(exc).__name__}: {exc}"))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the real LLM; compare tool trajectories only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    results = asyncio.run(run_cases(args.live))
    print("STATUS | CASE | DETAIL")
    print("-------|------|-------")
    for result in results:
        print(f"{'PASS' if result.passed else 'FAIL'} | {result.name} | {result.detail or '-'}")
    print(f"{sum(r.passed for r in results)}/{len(results)} passed ({'live trajectories only' if args.live else 'deterministic'})")
    return int(any(not result.passed for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
