import asyncio
import os
import sys
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.append(os.getcwd())

from backend.dumper.telemetry import ConnectionManager


def make_ws():
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


class TestConnectionManagerPerStore(unittest.IsolatedAsyncioTestCase):
    """Phase 6: WS telemetry is grouped by store_id — store A's dashboard
    must not see store B's price/stock events, and vice versa."""

    def setUp(self):
        self.manager = ConnectionManager()

    async def test_connect_accepts_and_registers_under_store_id(self):
        ws = make_ws()
        await self.manager.connect(ws, "store-a")
        ws.accept.assert_awaited_once()
        self.assertIn(ws, self.manager.active_connections["store-a"])

    async def test_broadcast_only_reaches_clients_of_the_same_store(self):
        ws_a = make_ws()
        ws_b = make_ws()
        await self.manager.connect(ws_a, "store-a")
        await self.manager.connect(ws_b, "store-b")

        await self.manager.broadcast("price_update", {"sku": "X1"}, store_id="store-a")

        ws_a.send_json.assert_awaited_once()
        ws_b.send_json.assert_not_awaited()

    async def test_none_store_id_is_the_legacy_shared_channel(self):
        """Clients with no resolvable store (no JWT, or JWT but no store yet)
        all land in the same store_id=None channel — same behavior as before
        Phase 6 for every client."""
        ws1 = make_ws()
        ws2 = make_ws()
        await self.manager.connect(ws1, None)
        await self.manager.connect(ws2)  # default also None

        await self.manager.broadcast("log", {"message": "hi"})  # default store_id=None

        ws1.send_json.assert_awaited_once()
        ws2.send_json.assert_awaited_once()

    async def test_broadcast_to_store_with_no_clients_is_a_noop(self):
        # No exception, no-op — mirrors the old "if not active_connections: return" guard.
        await self.manager.broadcast("price_update", {"sku": "X1"}, store_id="store-with-nobody-watching")

    async def test_disconnect_removes_only_from_its_own_store(self):
        ws_a = make_ws()
        ws_b = make_ws()
        await self.manager.connect(ws_a, "store-a")
        await self.manager.connect(ws_b, "store-b")

        self.manager.disconnect(ws_a, "store-a")

        self.assertNotIn("store-a", self.manager.active_connections)
        self.assertIn(ws_b, self.manager.active_connections["store-b"])

    async def test_dead_connection_during_broadcast_is_pruned(self):
        ws_good = make_ws()
        ws_dead = make_ws()
        ws_dead.send_json.side_effect = Exception("connection reset")
        await self.manager.connect(ws_good, "store-a")
        await self.manager.connect(ws_dead, "store-a")

        await self.manager.broadcast("log", {"message": "hi"}, store_id="store-a")

        self.assertIn(ws_good, self.manager.active_connections["store-a"])
        self.assertNotIn(ws_dead, self.manager.active_connections.get("store-a", []))


class TestResolveWsStoreId(unittest.IsolatedAsyncioTestCase):
    """resolve_ws_store_id() — JWT comes in via ?token= query param (browser
    WebSocket API can't set Authorization headers), not the header."""

    def _make_ws(self, token=None):
        from starlette.datastructures import QueryParams, Headers
        ws = MagicMock()
        ws.query_params = QueryParams({"token": token} if token else {})
        ws.headers = Headers({})
        return ws

    async def test_no_token_returns_none(self):
        from backend.security.store_context import resolve_ws_store_id
        db = AsyncMock()
        result = await resolve_ws_store_id(self._make_ws(), db)
        self.assertIsNone(result)
        db.execute.assert_not_called()

    async def test_invalid_token_returns_none(self):
        from backend.security.store_context import resolve_ws_store_id
        db = AsyncMock()
        result = await resolve_ws_store_id(self._make_ws("not.a.valid.jwt"), db)
        self.assertIsNone(result)

    async def test_refresh_type_token_rejected(self):
        from backend.security.store_context import resolve_ws_store_id
        from backend.security.auth import create_refresh_token
        refresh_tok = create_refresh_token({"sub": "user-1"})
        db = AsyncMock()
        result = await resolve_ws_store_id(self._make_ws(refresh_tok), db)
        self.assertIsNone(result)

    async def test_valid_token_but_user_not_found_returns_none(self):
        from backend.security.store_context import resolve_ws_store_id
        from backend.security.auth import create_access_token
        access_tok = create_access_token({"sub": "ghost-user"})
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute.return_value = result_mock
        result = await resolve_ws_store_id(self._make_ws(access_tok), db)
        self.assertIsNone(result)

    async def test_valid_token_and_store_resolves_store_id(self):
        from backend.security.store_context import resolve_ws_store_id
        from backend.security.auth import create_access_token
        from backend.db.models import User, Store

        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-1", merchant_id="m_123", name="Shop")

        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = user
        store_result = MagicMock()
        store_result.scalars.return_value.first.return_value = store

        db = AsyncMock()
        db.execute.side_effect = [user_result, store_result]

        result = await resolve_ws_store_id(self._make_ws(access_tok), db)
        self.assertEqual(result, "store-1")


class TestEmitTelemetrySyncFromWorkerThread(unittest.IsolatedAsyncioTestCase):
    """
    Regression for the category-0 perf fix that moved
    RateLimitExecutorWorker.execute_updates onto asyncio.to_thread
    (backend/dumper/__init__.py::run_price_dumping_job) — executor.py calls
    emit_telemetry_sync() from inside that worker thread. Before the fix,
    emit_telemetry_sync used asyncio.get_event_loop(), which cannot find the
    running loop from a worker thread; the failure was swallowed by a bare
    `except: pass`, so telemetry would silently vanish with no error anywhere.
    set_main_loop() fixes this by capturing an explicit loop reference at
    startup instead of relying on thread-local loop discovery.
    """

    def setUp(self):
        import backend.dumper.telemetry as telemetry
        self.telemetry = telemetry
        self.telemetry.telemetry_manager.active_connections.clear()
        self._orig_main_loop = telemetry._main_loop

    def tearDown(self):
        self.telemetry._main_loop = self._orig_main_loop

    async def test_emit_from_worker_thread_reaches_connected_client(self):
        ws = make_ws()
        await self.telemetry.telemetry_manager.connect(ws, "store-a")
        self.telemetry.set_main_loop(asyncio.get_running_loop())

        done = threading.Event()

        def worker():
            self.telemetry.emit_telemetry_sync(
                "terminal_log", {"message": "hi from thread"}, store_id="store-a"
            )
            done.set()

        t = threading.Thread(target=worker)
        t.start()
        self.assertTrue(done.wait(timeout=2), "worker thread did not finish")
        t.join(timeout=2)

        # run_coroutine_threadsafe only schedules the coroutine onto the
        # loop — give this coroutine's own event loop a tick to run it.
        for _ in range(10):
            await asyncio.sleep(0.02)
            if ws.send_json.await_count:
                break

        ws.send_json.assert_awaited_once()
        sent = ws.send_json.call_args.args[0]
        self.assertEqual(sent["type"], "terminal_log")
        self.assertEqual(sent["data"]["message"], "hi from thread")

    async def test_emit_without_main_loop_set_does_not_raise(self):
        self.telemetry.set_main_loop(None)
        # Must not raise even though there is nowhere to deliver the event —
        # this is the pre-fix behavior (silent no-op), just via an explicit
        # None check now instead of a get_event_loop() exception.
        self.telemetry.emit_telemetry_sync("terminal_log", {"message": "hi"})


if __name__ == "__main__":
    unittest.main()
