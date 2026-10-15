import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.assistant import config, engine, runs, storage
from backend.assistant.config import AssistantConfig
from backend.assistant.engine import AssistantTurn, run_turn
from backend.assistant.prompt import StoreBrief
from backend.assistant.provider import LLMResult, ToolCall
from backend.assistant.tools import ToolContext
from backend.db.database import get_db
from backend.db.models import Base, Store, User, AssistantRunDB, AssistantMessageDB, AssistantPriceProposalDB
from backend.routers import assistant as router_module
from backend.security.store_context import StoreContext, get_store_context
from evals.run import ScriptedProvider, forbidden


def cfg(**overrides):
    return AssistantConfig(**dict(dict(enabled=True, provider='stub', model='test', api_key='', base_url='',
        max_tool_iterations=2, history_limit=10, request_timeout=1, max_output_tokens=100,
        allow_write_tools=False), **overrides))


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    monkeypatch.setattr(config, 'load_env', lambda: None)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)


@pytest.fixture
def database(tmp_path):
    @asynccontextmanager
    async def opened():
        sql_engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'runs.db').as_posix()}")
        @event.listens_for(sql_engine.sync_engine, 'connect')
        def fk(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')
        try:
            async with sql_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(sql_engine, expire_on_commit=False)() as db:
                db.add_all([Store(id=s, merchant_id=s, name=s) for s in ('a', 'b')])
                db.add(User(id='human', email='runs@example.test', password_hash='unused'))
                await db.commit()
                yield db, sql_engine
        finally:
            await sql_engine.dispose()
    return opened


def test_steps_timing_privacy_and_usage(monkeypatch):
    ticks = iter(i / 100 for i in range(8))
    monkeypatch.setattr(engine, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))
    async def execute(*args, **kwargs):
        return {'ok': True, 'result': {'private': 'TOOL_RESULT'}}
    monkeypatch.setattr(engine.tool_registry, 'execute', execute)
    provider = ScriptedProvider([
        {'tool_calls': [{'name': 'search_products', 'arguments': {'query': 'SECRET_ARGUMENT'}}]},
        {'text': 'SECRET_MESSAGE'}])
    turn = asyncio.run(run_turn('PRIVATE_INPUT', [], StoreBrief(store_id='a'), ToolContext(store_id='a'), cfg(), provider))
    assert turn.duration_ms == pytest.approx(70)
    assert turn.llm_calls == 2
    assert [s['kind'] for s in turn.steps] == ['llm', 'tool', 'llm']
    assert [s['duration_ms'] for s in turn.steps] == pytest.approx([10, 10, 10])
    assert all(s['ok'] for s in turn.steps)
    raw = json.dumps(turn.steps)
    assert all(secret not in raw for secret in ('SECRET_ARGUMENT', 'TOOL_RESULT', 'PRIVATE_INPUT', 'SECRET_MESSAGE'))
    assert all(set(s) == {'kind', 'name', 'duration_ms', 'ok'} for s in turn.steps)


def test_tool_validation_and_llm_errors_are_timed_and_bounded():
    async def scenario():
        turn = await run_turn('test', [], StoreBrief(store_id='a'), ToolContext(store_id='a'), cfg(), ScriptedProvider([
            {'tool_calls': [{'name': 'get_sales_stats', 'arguments': {}}]}, {'error': 'E' * 800}]))
        assert turn.error == 'E' * 800
        assert [s['ok'] for s in turn.steps] == [True, False, False]
        assert len(turn.steps[-1]['error']) == 200
        assert all(s['duration_ms'] >= 0 for s in turn.steps)
    asyncio.run(scenario())


@pytest.mark.parametrize('prices,expected', [((None,None),None), ((2,None),None), ((2,4),.001), ((0,0),0)])
def test_run_cost_and_sanitized_storage(database, prices, expected):
    async def scenario():
        async with database() as (db, _):
            turn = AssistantTurn(reply='private', provider='scripted', duration_ms=123, llm_calls=1,
                usage={'model':'real-model','prompt_tokens':100,'completion_tokens':200},
                steps=[{'kind':'tool','name':'test','duration_ms':1,'ok':False,'error':'E'*600,
                        'arguments':{'private':1},'result':'private'}], error='F'*700)
            row = await runs.save_run(db, store_id='a',conversation_id=None,user_id='human',
                started_at=datetime.utcnow(),turn=turn,cfg=cfg(price_prompt_per_1m=prices[0],price_completion_per_1m=prices[1]))
            await db.commit()
            assert row.cost_usd == expected
            assert row.duration_ms == 123 and row.llm_calls == 1 and row.tool_calls == row.tool_errors == 1
            assert row.model == 'real-model' and row.prompt_tokens == 100 and row.completion_tokens == 200
            assert len(row.error) == 500
            steps = json.loads(row.steps)
            assert len(steps[0]['error']) == 200
            assert 'arguments' not in steps[0] and 'result' not in steps[0]
    asyncio.run(scenario())


@pytest.mark.parametrize('value,expected', [('',None),('abc',None),('-1',None),('nan',None),('inf',None),('0',0),('2.5',2.5)])
def test_env_prices(monkeypatch,value,expected):
    monkeypatch.setenv('ASSISTANT_PRICE_PROMPT_PER_1M',value)
    monkeypatch.setenv('ASSISTANT_PRICE_COMPLETION_PER_1M',value)
    result = config.load_config()
    assert result.price_prompt_per_1m == result.price_completion_per_1m == expected


@asynccontextmanager
async def client_for(db, store_ctx):
    app = FastAPI()
    app.include_router(router_module.router)
    async def override_db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_store_context] = lambda: store_ctx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client


def store_context():
    return StoreContext(token='',merchant_id='a',store_id='a',role='VIEWER',sections=frozenset({'assistant'}))


def test_routes_isolation_limits_summary_and_percentiles(database):
    async def scenario():
        async with database() as (db, _):
            now = datetime.utcnow()
            for i, duration in enumerate((10,20,30,40,50)):
                db.add(AssistantRunDB(id=f'a{i}',store_id='a',started_at=now,duration_ms=duration,
                    prompt_tokens=10, completion_tokens=5,cost_usd=.1 if i else None,
                    error='failed' if i==0 else None,truncated=i==1,
                    tool_errors=int(i==2), steps=json.dumps([{'kind':'tool','name':'search','ok':i!=2,'duration_ms':1}])))
            db.add(AssistantRunDB(id='foreign',store_id='b',started_at=now,duration_ms=9999,prompt_tokens=9999,cost_usd=99))
            db.add(AssistantRunDB(id='old',store_id='a',started_at=now-timedelta(days=10),duration_ms=9999,prompt_tokens=9999))
            await db.commit()
            ctx = store_context()
            async with client_for(db,ctx) as client:
                path='/api/v1/assistant'
                listed = (await client.get(path+'/runs?limit=2')).json()['runs']
                assert len(listed)==2 and all(r['store_id']=='a' and 'steps' not in r for r in listed)
                assert len((await client.get(path+'/runs')).json()['runs'])==6
                assert (await client.get(path+'/runs/foreign')).status_code==404
                detail=(await client.get(path+'/runs/a0')).json()['run']
                assert detail['steps'][0]['name']=='search'
                result=(await client.get(path+'/metrics/summary')).json()
                assert result['runs']==5 and result['prompt_tokens']==50 and result['completion_tokens']==25
                assert result['cost_usd']==pytest.approx(.4) and result['priced_runs']==4
                assert result['error_rate']==.4 and result['truncated_rate']==.2
                assert result['p50_duration_ms']==30 and result['p95_duration_ms']==48
                assert result['tool_errors']=={'search':1} and result['top_tools']==[{'name':'search','calls':5}]
                assert result['sample']['rows']==5 and result['sample']['limit']==5000
                for query in ('limit=0','limit=101','limit=x'):
                    assert (await client.get(path+'/runs?'+query)).status_code==422
                for query in ('days=0','days=91','days=x'):
                    assert (await client.get(path+'/metrics/summary?'+query)).status_code==422
                ctx.store_id='b'
                other=(await client.get(path+'/metrics/summary?days=1')).json()
                assert other['runs']==1 and other['prompt_tokens']==9999
                assert (await client.get(path+'/runs/a0')).status_code==404
                assert [r['id'] for r in (await client.get(path+'/runs')).json()['runs']]==['foreign']
                ctx.sections=frozenset()
                for route in ('/runs','/runs/foreign','/metrics/summary'):
                    assert (await client.get(path+route)).status_code==403
    asyncio.run(scenario())


def test_summary_bounded_sample_and_empty(database,monkeypatch):
    async def scenario():
        async with database() as (db, _):
            empty=await runs.summary(db,'a',7)
            assert empty['runs']==0 and empty['cost_usd'] is None and empty['p50_duration_ms'] is None
            now=datetime.utcnow()
            db.add_all([AssistantRunDB(store_id='a',started_at=now+timedelta(seconds=i),duration_ms=i,prompt_tokens=10) for i in range(5)])
            await db.flush()
            monkeypatch.setattr(runs,'SAMPLE_LIMIT',2)
            result=await runs.summary(db,'a',7)
            assert result['runs']==5 and result['prompt_tokens']==50
            assert result['p50_duration_ms']==3.5
            assert result['sample']['truncated'] and result['sample']['rows']==2
            assert result['cost_usd'] is None
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['none','insert','serialization','llm'])
def test_send_message_persists_or_isolates_trace_failure(database,monkeypatch,failure):
    async def scenario():
        async with database() as (db, sql_engine):
            convo=await storage.create_conversation(db,'a','human')
            await db.commit()
            convo_id=convo.id
            monkeypatch.setattr(router_module,'load_config',lambda:cfg())
            monkeypatch.setattr(router_module,'_request_user_id',lambda request:'human')
            async def turn(**kwargs):
                if failure == 'llm':
                    db.add(AssistantPriceProposalDB(store_id='a', conversation_id=convo_id, sku='SKU',
                        old_price=150, new_price=160, min_price_at_creation=100, reason='test',
                        status='pending', created_by_user_id='human'))
                    await db.flush()
                return AssistantTurn(reply='reply',provider='test',error='provider failed' if failure=='llm' else None,
                    duration_ms=12,llm_calls=1,steps=[{'kind':'llm','name':'test','duration_ms':12,'ok':failure!='llm'}])
            monkeypatch.setattr(router_module,'run_turn',turn)
            if failure=='insert':
                @event.listens_for(sql_engine.sync_engine,'before_cursor_execute')
                def break_insert(conn,cursor,statement,parameters,context,executemany):
                    if statement.startswith('INSERT INTO assistant_runs'):
                        raise RuntimeError('injected trace insert failure')
            if failure=='serialization':
                async def broken(*args,**kwargs):
                    raise ValueError('injected serializer failure')
                monkeypatch.setattr(runs,'save_run',broken)
            async with client_for(db,store_context()) as client:
                response=await client.post(f'/api/v1/assistant/conversations/{convo_id}/messages',json={'text':'hello'})
                if failure=='llm':
                    assert response.status_code==502 and response.json()['detail']=='provider failed'
                else:
                    assert response.status_code==200 and response.json()['messages'][1]['content']=='reply'
            assert (await db.execute(select(func.count()).select_from(AssistantRunDB))).scalar_one()==int(failure in ('none','llm'))
            assert (await db.execute(select(func.count()).select_from(AssistantMessageDB))).scalar_one()==(0 if failure=='llm' else 2)
            assert (await db.execute(select(func.count()).select_from(AssistantPriceProposalDB))).scalar_one()==0
            if failure=='none':
                row=(await db.execute(select(AssistantRunDB))).scalar_one()
                assert row.conversation_id==convo_id and row.user_id=='human' and row.cost_usd is None
                assert row.started_at <= datetime.utcnow() and row.duration_ms==12 and row.llm_calls==1
    asyncio.run(scenario())


def test_migration_chain():
    from alembic.script import ScriptDirectory
    scripts=ScriptDirectory(str(Path(__file__).resolve().parents[1]/'alembic'))
    assert scripts.get_heads()==['051_assistant_runs']
    assert scripts.get_revision('051_assistant_runs').down_revision=='050_assistant_price_proposals'

def test_run_conversation_fk_set_null(database):
    async def scenario():
        async with database() as (db, _):
            convo = await storage.create_conversation(db, 'a', 'human')
            row = await runs.save_run(db, store_id='a', conversation_id=convo.id, user_id='human',
                started_at=datetime.utcnow(), turn=AssistantTurn(reply='test'), cfg=cfg())
            run_id = row.id
            await db.commit()
            await db.execute(text('DELETE FROM assistant_conversations WHERE id=:id'), {'id': convo.id})
            await db.commit()
            await db.refresh(row)
            assert row.id == run_id and row.conversation_id is None
    asyncio.run(scenario())


def test_final_llm_timing_and_usage(monkeypatch):
    class Provider:
        name = 'fixture'
        def __init__(self):
            self.calls = 0
        async def complete(self, messages, tools=None):
            self.calls += 1
            usage = {'model': 'fixture-model', 'prompt_tokens': 10, 'completion_tokens': 5}
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id='call',name='get_sales_stats',arguments={})],usage=usage)
            assert tools is None
            return LLMResult(error='failure',usage=usage)
    ticks = iter(i / 100 for i in range(8))
    monkeypatch.setattr(engine,'time',SimpleNamespace(monotonic=lambda:next(ticks)))
    turn = asyncio.run(run_turn('test', [], StoreBrief(store_id='a'), ToolContext(store_id='a'),
        cfg(max_tool_iterations=0), Provider()))
    assert turn.truncated and turn.llm_calls == 2
    assert turn.duration_ms == pytest.approx(70)
    assert turn.usage == {'model': 'fixture-model', 'prompt_tokens': 20, 'completion_tokens': 10}
    assert len(turn.steps) == 3 and turn.steps[-1]['ok'] is False
