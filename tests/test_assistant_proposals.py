import asyncio
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import requests
from sqlalchemy import event, select, update, delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.assistant import proposals as p, proposals_tool, tools
from backend.assistant.tools import ToolContext
from backend.db.models import (Base, Store, StoreProductDB, DumpingConfigDB,
                               AssistantPriceProposalDB, User)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in assistant proposal tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr("backend.assistant.config.load_env", lambda: None)


@pytest.fixture
def database(tmp_path):
    @asynccontextmanager
    async def opened():
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'proposals.db').as_posix()}")
        @event.listens_for(engine.sync_engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                db.add_all([Store(id=s, merchant_id=s, name=s) for s in ("a", "b")])
                db.add(User(id="human", email="human@example.test", password_hash="unused"))
                await db.flush()
                db.add(StoreProductDB(store_id="a", payload=json.dumps([
                    {"merchantSku": "SKU", "sku": "KASPI", "price_num": 150}])))
                db.add(DumpingConfigDB(store_id="a", sku="SKU", min_price=100, max_price=200))
                await db.commit()
                yield db, ToolContext(store_id="a", user_id="human", role="MANAGER", confirmed=True, db=db), factory
        finally:
            await engine.dispose()
    return opened


async def create(db, ctx, **overrides):
    args = dict(sku="SKU", new_price=160, reason="Проверка")
    args.update(overrides)
    return await p.create_proposal(db, ctx, **args)


def test_create_valid_proposal_and_alias_config_priority(database):
    async def scenario():
        async with database() as (db, ctx, _):
            db.add(DumpingConfigDB(store_id="a", sku="KASPI", min_price=190))
            await db.flush()
            row = await create(db, ctx, sku="KASPI")
            assert (row.sku, row.old_price, row.new_price, row.status) == ("SKU", 150, 160, "pending")
            assert (row.min_price_at_creation, row.max_price_at_creation) == (100, 200)
            assert row.created_by_user_id == "human" and row.decided_at is None
            assert row.apply_result is None
            catalog = (await db.execute(select(StoreProductDB.payload).where(StoreProductDB.store_id == "a"))).scalar_one()
            assert json.loads(catalog)[0]["price_num"] == 150
    run(scenario())


@pytest.mark.parametrize("overrides,match", [
    ({"new_price": 99}, "ниже минимальной"), ({"new_price": 201}, "выше максимальной"),
    ({"sku": "unknown"}, "SKU не найден"), ({"new_price": 150}, "равна текущей"),
    ({"new_price": 160.5}, "целым"), ({"new_price": True}, "целым"),
    ({"new_price": 0}, "целым"), ({"new_price": float("nan")}, "целым"),
    ({"new_price": float("inf")}, "целым"), ({"new_price": "160"}, "целым"),
    ({"new_price": 10 ** 400}, "целым"),
    ({"reason": "x" * 301}, "300"), ({"reason": " "}, "300"),
])
def test_invalid_proposals(database, overrides, match):
    async def scenario():
        async with database() as (db, ctx, _):
            with pytest.raises(p.ProposalError, match=match):
                await create(db, ctx, **overrides)
            assert await p.list_proposals(db, ctx) == []
    run(scenario())


@pytest.mark.parametrize("minimum", [None, 0, -1])
def test_missing_minimum(database, minimum):
    async def scenario():
        async with database() as (db, ctx, _):
            if minimum is None:
                await db.execute(delete(DumpingConfigDB).where(DumpingConfigDB.store_id == "a"))
            else:
                await db.execute(update(DumpingConfigDB).where(DumpingConfigDB.store_id == "a").values(min_price=minimum))
            with pytest.raises(p.ProposalError, match="Минимальная цена.*не задана"):
                await create(db, ctx)
    run(scenario())


def test_store_isolation(database):
    async def scenario():
        async with database() as (db, ctx, _):
            row = await create(db, ctx)
            foreign = ToolContext(store_id="b", user_id="human", role="MANAGER")
            assert await p.list_proposals(db, foreign) == []
            for action in (p.get_proposal, p.reject_proposal):
                with pytest.raises(p.ProposalNotFound):
                    await action(db, foreign, row.id)
            with pytest.raises(p.ProposalNotFound):
                await p.approve_proposal(db, foreign, row.id, p.DryRunGateway())
            assert (await p.get_proposal(db, ctx, row.id)).status == "pending"
    run(scenario())


def test_dry_run_and_double_decision(database, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No HTTP clients allowed")
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    async def scenario():
        async with database() as (db, ctx, _):
            row = await create(db, ctx)
            # Windows asyncio creates its own local socketpair when starting
            # the loop. Block raw sockets after that initialization.
            with monkeypatch.context() as raw_socket_guard:
                raw_socket_guard.setattr(socket.socket, "connect", forbidden)
                row = await p.approve_proposal(db, ctx, row.id, p.DryRunGateway())
            assert row.status == "approved" and row.decided_by_user_id == "human"
            assert json.loads(row.apply_result)["price_changed"] is False
            await db.commit()
            for action in (p.approve_proposal, p.reject_proposal):
                with pytest.raises(p.ProposalConflict):
                    if action is p.approve_proposal:
                        await action(db, ctx, row.id, p.DryRunGateway())
                    else:
                        await action(db, ctx, row.id)
            payload = (await db.execute(select(StoreProductDB.payload).where(StoreProductDB.store_id == "a"))).scalar_one()
            assert json.loads(payload)[0]["price_num"] == 150
    run(scenario())


def test_approve_revalidates_current_config(database):
    class NeverGateway:
        async def apply(self, row):
            pytest.fail("Gateway must not run after validation rejection")
    async def scenario():
        async with database() as (db, ctx, factory):
            row = await create(db, ctx)
            await db.commit()
            async with factory() as other:
                await other.execute(update(DumpingConfigDB).where(
                    DumpingConfigDB.store_id == "a").values(min_price=180))
                await other.commit()
            row = await p.approve_proposal(db, ctx, row.id, NeverGateway())
            assert row.status == "rejected"
            assert "ниже минимальной" in json.loads(row.apply_result)["error"]
    run(scenario())


def test_gateway_failure_persisted(database):
    class BrokenGateway:
        async def apply(self, row):
            raise RuntimeError("test gateway failure")
    async def scenario():
        async with database() as (db, ctx, _):
            row = await create(db, ctx)
            row = await p.approve_proposal(db, ctx, row.id, BrokenGateway())
            assert row.status == "failed"
            assert json.loads(row.apply_result)["error"] == "test gateway failure"
            await db.commit()
            assert (await p.get_proposal(db, ctx, row.id)).status == "failed"
    run(scenario())


def test_tool_gates_and_result(database, monkeypatch):
    async def scenario():
        async with database() as (db, ctx, _):
            args = {"sku": "SKU", "new_price": 160, "reason": "test"}
            ctx.confirmed = False
            spec = tools.get("propose_price")
            assert spec.read_only is False and spec.min_role == "MANAGER"
            assert spec.requires_confirmation is False
            monkeypatch.setenv("ASSISTANT_ALLOW_WRITE_TOOLS", "0")
            assert not (await tools.execute("propose_price", args, ctx, allow_write=False))["ok"]
            assert not (await tools.execute("propose_price", args, ctx, allow_write=True))["ok"]
            monkeypatch.setenv("ASSISTANT_ALLOW_WRITE_TOOLS", "1")
            ctx.role = "VIEWER"
            assert not (await tools.execute("propose_price", args, ctx, allow_write=True))["ok"]
            with pytest.raises(PermissionError):
                await create(db, ctx)
            ctx.role = "MANAGER"
            assert not (await tools.execute("propose_price", dict(args, store_id="b"), ctx, allow_write=True))["ok"]
            assert await p.list_proposals(db, ctx) == []
            result = await tools.execute("propose_price", args, ctx, allow_write=True)
            assert result["ok"] and result["result"]["status"] == "pending"
            assert "цена НЕ изменена" in result["result"]["message"]
    run(scenario())


@pytest.mark.parametrize("allow_write,role,new_price,sections,error", [
    (True, "MANAGER", 160, frozenset({"assistant"}), None),
    (False, "MANAGER", 160, frozenset({"assistant"}), "ASSISTANT_ALLOW_WRITE_TOOLS=0"),
    (True, "VIEWER", 160, frozenset({"assistant"}), "Недостаточно прав"),
    (True, "MANAGER", 99, frozenset({"assistant"}), "ниже минимальной"),
    (True, "MANAGER", 160, frozenset(), "Раздел 'assistant'"),
])
def test_engine_proposal_without_chat_confirmation(database, monkeypatch, allow_write, role, new_price, sections, error):
    from backend.assistant.config import AssistantConfig
    from backend.assistant.engine import run_turn
    from backend.assistant.prompt import StoreBrief
    from evals.run import ScriptedProvider

    def forbidden(*args, **kwargs):
        pytest.fail("Agent must not access approval or gateway")

    monkeypatch.setenv("ASSISTANT_ALLOW_WRITE_TOOLS", "1" if allow_write else "0")
    monkeypatch.setattr(p, "get_gateway", forbidden)
    monkeypatch.setattr(p, "approve_proposal", forbidden)
    monkeypatch.setattr(p.DryRunGateway, "apply", forbidden)

    async def scenario():
        async with database() as (db, ctx, _):
            ctx.confirmed = False
            ctx.role = role
            ctx.sections = sections
            cfg = AssistantConfig(enabled=True, provider="stub", model="test", api_key="",
                base_url="", max_tool_iterations=2, history_limit=10, request_timeout=5,
                max_output_tokens=100, allow_write_tools=allow_write)
            provider = ScriptedProvider([
                {"tool_calls": [{"name": "propose_price", "arguments": {
                    "sku": "SKU", "new_price": new_price, "reason": "Запрос пользователя"}}]},
                {"text": "Результат получен."},
            ])
            turn = await run_turn(f"Подними цену SKU до {new_price}", [],
                                  StoreBrief(store_id=ctx.store_id), ctx, cfg, provider)
            assert turn.error is None
            assert len(turn.tool_trace) == 1
            result = turn.tool_trace[0].result
            rows = list((await db.execute(select(AssistantPriceProposalDB))).scalars())
            if error:
                assert result["ok"] is False and error in result["error"]
                assert rows == []
            else:
                assert result["ok"] is True
                assert len(rows) == 1
                row = rows[0]
                assert (row.id, row.status) == (result["result"]["id"], "pending")
                assert (row.sku, row.old_price, row.new_price) == ("SKU", 150, 160)
                assert row.decided_at is None and row.apply_result is None
            payload = (await db.execute(select(StoreProductDB.payload).where(
                StoreProductDB.store_id == ctx.store_id))).scalar_one()
            assert json.loads(payload)[0]["price_num"] == 150
    run(scenario())


def test_pending_limit_and_reject(database, monkeypatch):
    monkeypatch.setenv("ASSISTANT_MAX_PENDING_PROPOSALS", "1")
    async def scenario():
        async with database() as (db, ctx, _):
            row = await create(db, ctx)
            with pytest.raises(p.ProposalError, match="лимит"):
                await create(db, ctx)
            ctx.role = "VIEWER"
            with pytest.raises(PermissionError):
                await p.reject_proposal(db, ctx, row.id)
            with pytest.raises(PermissionError):
                await p.approve_proposal(db, ctx, row.id, p.DryRunGateway())
            ctx.role = "MANAGER"
            assert (await p.reject_proposal(db, ctx, row.id)).status == "rejected"
            with pytest.raises(p.ProposalConflict):
                await p.reject_proposal(db, ctx, row.id)
            assert (await create(db, ctx)).status == "pending"
            assert len(await p.list_proposals(db, ctx, "pending")) == 1
    run(scenario())


@pytest.mark.parametrize("name", ["real", "", "DRY_RUN", " dry_run"])
def test_gateway_configuration_fails_closed(monkeypatch, name):
    monkeypatch.setenv("ASSISTANT_PRICE_GATEWAY", name)
    with pytest.raises(ValueError, match="только dry_run"):
        p.get_gateway()


def test_default_gateway_and_limit(monkeypatch):
    from backend.assistant.config import max_pending_proposals
    monkeypatch.delenv("ASSISTANT_PRICE_GATEWAY", raising=False)
    monkeypatch.delenv("ASSISTANT_MAX_PENDING_PROPOSALS", raising=False)
    assert isinstance(p.get_gateway(), p.DryRunGateway)
    assert max_pending_proposals() == 50


def test_migration_single_head():
    from alembic.script import ScriptDirectory
    directory = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
    assert directory.get_heads() == ["053_chat_response_log_llm_trace"]
    assert directory.get_revision("050_assistant_price_proposals").down_revision == "049_stock_group_float_stock"
    # The old head was the only revision without a child before adding 050.
    revisions = [r for r in directory.walk_revisions() if r.revision not in {"050_assistant_price_proposals", "051_assistant_runs", "052_store_card_facts", "053_chat_response_log_llm_trace"}]
    parents = {r.down_revision for r in revisions if isinstance(r.down_revision, str)}
    assert {r.revision for r in revisions} - parents == {"049_stock_group_float_stock"}


def test_concurrent_approvals_call_gateway_once(database):
    class CountingGateway:
        calls = 0
        async def apply(self, row):
            self.calls += 1
            await asyncio.sleep(0)
            return {"price_changed": False}
    async def scenario():
        async with database() as (db, ctx, factory):
            row = await create(db, ctx)
            await db.commit()
            gateway = CountingGateway()
            async def approve():
                async with factory() as session:
                    try:
                        result = await p.approve_proposal(session, ctx, row.id, gateway)
                        await session.commit()
                        return result.status
                    except p.ProposalConflict:
                        await session.rollback()
                        return "conflict"
            assert sorted(await asyncio.gather(approve(), approve())) == ["approved", "conflict"]
            assert gateway.calls == 1
    run(scenario())


def test_concurrent_pending_limit(database, monkeypatch):
    monkeypatch.setenv("ASSISTANT_MAX_PENDING_PROPOSALS", "1")
    async def scenario():
        async with database() as (db, ctx, factory):
            async def propose():
                async with factory() as session:
                    try:
                        await create(session, ctx)
                        await session.commit()
                        return "created"
                    except p.ProposalError as exc:
                        await session.rollback()
                        assert "лимит" in str(exc)
                        return "limited"
            assert sorted(await asyncio.gather(propose(), propose())) == ["created", "limited"]
            assert len(await p.list_proposals(db, ctx, "pending")) == 1
    run(scenario())


@pytest.mark.parametrize("change", ["current", "maximum", "missing_sku", "missing_minimum"])
def test_approve_revalidates_catalog_and_other_bounds(database, change):
    async def scenario():
        async with database() as (db, ctx, factory):
            row = await create(db, ctx)
            await db.commit()
            async with factory() as other:
                if change in ("current", "missing_sku"):
                    products = [{"merchantSku": "SKU", "price_num": 160}] if change == "current" else []
                    await other.execute(update(StoreProductDB).where(StoreProductDB.store_id == "a").values(payload=json.dumps(products)))
                elif change == "maximum":
                    await other.execute(update(DumpingConfigDB).where(DumpingConfigDB.store_id == "a").values(max_price=155))
                else:
                    await other.execute(delete(DumpingConfigDB).where(DumpingConfigDB.store_id == "a"))
                await other.commit()
            row = await p.approve_proposal(db, ctx, row.id, p.DryRunGateway())
            assert row.status == "rejected" and "error" in json.loads(row.apply_result)
    run(scenario())


def test_router_permissions_store_scope_and_decisions(database, monkeypatch):
    from fastapi import FastAPI
    from backend.routers import assistant as router_module
    from backend.db.database import get_db
    from backend.security.store_context import StoreContext, get_store_context

    async def scenario():
        async with database() as (db, ctx, _):
            row = await create(db, ctx)
            await db.commit()
            app = FastAPI()
            app.include_router(router_module.router)
            store_ctx = StoreContext(token="", merchant_id="a", store_id="a", role="VIEWER",
                                     sections=frozenset({"assistant"}))
            async def override_db():
                try:
                    yield db
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
            app.dependency_overrides[get_db] = override_db
            app.dependency_overrides[get_store_context] = lambda: store_ctx
            monkeypatch.setenv("ASSISTANT_PRICE_GATEWAY", "dry_run")
            from backend.security.auth import create_access_token
            headers = {"Authorization": "Bearer " + create_access_token({"sub": "human"})}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers) as client:
                path = f"/api/v1/assistant/proposals/{row.id}"
                assert (await client.get("/api/v1/assistant/proposals?status=pending")).json()["proposals"][0]["id"] == row.id
                for decision in ("approve", "reject"):
                    assert (await client.post(f"{path}/{decision}")).status_code == 403
                store_ctx.role = "MANAGER"
                store_ctx.sections = frozenset()
                assert (await client.get("/api/v1/assistant/proposals")).status_code == 403
                assert (await client.post(f"{path}/approve")).status_code == 403
                store_ctx.sections = frozenset({"assistant"})
                store_ctx.store_id = "b"
                assert (await client.get("/api/v1/assistant/proposals")).json()["proposals"] == []
                assert (await client.post(f"{path}/approve")).status_code == 404
                store_ctx.store_id = "a"
                monkeypatch.setenv("ASSISTANT_PRICE_GATEWAY", "real")
                assert (await client.post(f"{path}/approve")).status_code == 400
                monkeypatch.setenv("ASSISTANT_PRICE_GATEWAY", "dry_run")
                response = await client.post(f"{path}/approve")
                assert response.status_code == 200, response.text
                assert response.json()["proposal"]["apply_result"]["price_changed"] is False
                assert response.json()["proposal"]["decided_by_user_id"] == "human"
                assert (await client.post(f"{path}/approve")).status_code == 409
                assert (await client.get("/api/v1/assistant/proposals?status=wrong")).status_code == 400
    run(scenario())
