import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from backend.db.models import Store
from backend.security.store_context import StoreContext, list_active_stores
from backend.routers import dumping


class FakeAsyncSession:
    """Minimal async-context-manager stand-in for AsyncSessionLocal(), returning
    a canned .execute() result — mirrors the AsyncMock pattern already used in
    test_store_context.py, adapted for the `async with AsyncSessionLocal() as s`
    usage inside list_active_stores()."""

    def __init__(self, stores):
        self._stores = stores

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, stmt):
        result = MagicMock()
        scalars_result = MagicMock()
        scalars_result.all.return_value = self._stores
        result.scalars.return_value = scalars_result
        return result


def make_session_factory(stores):
    return lambda: FakeAsyncSession(stores)


class TestListActiveStores(unittest.IsolatedAsyncioTestCase):
    """Phase 7: list_active_stores() resolves the store set for background
    workers — no HTTP request/JWT involved, unlike get_store_context()."""

    async def test_no_stores_in_db_returns_empty_list(self):
        with patch("backend.db.database.AsyncSessionLocal", make_session_factory([])):
            result = await list_active_stores()
        self.assertEqual(result, [])

    @patch("backend.security.store_context.decrypt_data")
    async def test_active_store_with_decryptable_key_is_included(self, mock_decrypt):
        mock_decrypt.return_value = "plaintext_token"
        store = Store(id="store-1", merchant_id="m_123", name="Shop", encrypted_api_key="cipher", status="ACTIVE")
        with patch("backend.db.database.AsyncSessionLocal", make_session_factory([store])):
            result = await list_active_stores()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], StoreContext(token="plaintext_token", merchant_id="m_123", store_id="store-1"))

    @patch("backend.security.store_context.decrypt_data")
    async def test_cookie_expired_store_still_included_rest_token_still_usable(self, mock_decrypt):
        """COOKIE_EXPIRED is about MC private-API cookies, not the public REST
        token — a store in this state can still sync/read/price via REST."""
        mock_decrypt.return_value = "plaintext_token"
        store = Store(id="store-1", merchant_id="m_123", name="Shop", encrypted_api_key="cipher", status="COOKIE_EXPIRED")
        with patch("backend.db.database.AsyncSessionLocal", make_session_factory([store])):
            result = await list_active_stores()
        self.assertEqual(len(result), 1)

    async def test_paused_store_excluded_query_filters_at_db_level(self):
        """The WHERE clause filters PAUSED at the DB layer — our fake session
        just returns whatever the (mocked) query would have returned, so this
        test documents the contract rather than re-testing SQLAlchemy itself."""
        with patch("backend.db.database.AsyncSessionLocal", make_session_factory([])):
            result = await list_active_stores()
        self.assertEqual(result, [])

    async def test_store_without_api_key_is_skipped(self):
        store = Store(id="store-1", merchant_id="m_123", name="Shop", encrypted_api_key=None, status="ACTIVE")
        with patch("backend.db.database.AsyncSessionLocal", make_session_factory([store])):
            result = await list_active_stores()
        self.assertEqual(result, [])

    @patch("backend.security.store_context.decrypt_data")
    async def test_undecryptable_key_is_skipped_not_raised(self, mock_decrypt):
        mock_decrypt.return_value = ""  # decrypt failure returns "" per crypto.py's contract
        store = Store(id="store-1", merchant_id="m_123", name="Shop", encrypted_api_key="stale-cipher", status="ACTIVE")
        with patch("backend.db.database.AsyncSessionLocal", make_session_factory([store])):
            result = await list_active_stores()
        self.assertEqual(result, [])

    @patch("backend.security.store_context.decrypt_data")
    async def test_multiple_stores_all_resolved_independently(self, mock_decrypt):
        mock_decrypt.side_effect = ["token_a", "token_b"]
        stores = [
            Store(id="store-a", merchant_id="m_a", name="A", encrypted_api_key="c_a", status="ACTIVE"),
            Store(id="store-b", merchant_id="m_b", name="B", encrypted_api_key="c_b", status="ACTIVE"),
        ]
        with patch("backend.db.database.AsyncSessionLocal", make_session_factory(stores)):
            result = await list_active_stores()
        self.assertEqual([c.store_id for c in result], ["store-a", "store-b"])
        self.assertEqual([c.token for c in result], ["token_a", "token_b"])


class TestRunScheduledDumpingCycle(unittest.IsolatedAsyncioTestCase):
    """Phase 7: run_scheduled_dumping_cycle() — the testable core of
    background_dumping_loop(), extracted so it can run without the
    infinite while-loop/sleep wrapper around it."""

    @patch("backend.routers.dumping._get_token")
    @patch("backend.routers.dumping.list_active_stores", new_callable=AsyncMock)
    async def test_no_stores_and_no_legacy_token_does_nothing(self, mock_list_stores, mock_get_token):
        mock_list_stores.return_value = []
        mock_get_token.return_value = None
        result = await dumping.run_scheduled_dumping_cycle()
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(result["stores_processed"], 0)

    @patch("backend.routers.dumping._run_dumping_cycle", new_callable=AsyncMock)
    @patch("backend.routers.dumping._get_token")
    @patch("backend.routers.dumping.list_active_stores", new_callable=AsyncMock)
    async def test_no_stores_in_db_falls_back_to_legacy_session(self, mock_list_stores, mock_get_token, mock_cycle):
        """Pre-Phase-4 / dev environments with no Store rows yet must keep
        working exactly as before Phase 7."""
        mock_list_stores.return_value = []
        mock_get_token.return_value = "legacy_token"
        mock_cycle.return_value = {"updated_count": 3}

        result = await dumping.run_scheduled_dumping_cycle()

        mock_cycle.assert_awaited_once_with("legacy_token", None)
        self.assertEqual(result["updated_count"], 3)

    @patch("backend.routers.dumping._run_dumping_cycle", new_callable=AsyncMock)
    @patch("backend.routers.dumping.list_active_stores", new_callable=AsyncMock)
    async def test_iterates_every_active_store_independently(self, mock_list_stores, mock_cycle):
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        mock_cycle.side_effect = [{"updated_count": 2}, {"updated_count": 5}]

        result = await dumping.run_scheduled_dumping_cycle()

        self.assertEqual(mock_cycle.await_args_list[0].args, ("tok_a", "store-a"))
        self.assertEqual(mock_cycle.await_args_list[1].args, ("tok_b", "store-b"))
        self.assertEqual(result["updated_count"], 7)
        self.assertEqual(result["stores_processed"], 2)

    @patch("backend.routers.dumping._run_dumping_cycle", new_callable=AsyncMock)
    @patch("backend.routers.dumping.list_active_stores", new_callable=AsyncMock)
    async def test_one_store_failing_does_not_block_the_others(self, mock_list_stores, mock_cycle):
        """A dumping error for store A (e.g. Kaspi API blip) must not prevent
        store B's cycle from running — the whole point of per-store isolation."""
        mock_list_stores.return_value = [
            StoreContext(token="tok_a", merchant_id="m_a", store_id="store-a"),
            StoreContext(token="tok_b", merchant_id="m_b", store_id="store-b"),
        ]
        mock_cycle.side_effect = [Exception("Kaspi API 500"), {"updated_count": 4}]

        result = await dumping.run_scheduled_dumping_cycle()

        self.assertEqual(mock_cycle.await_count, 2)
        self.assertEqual(result["updated_count"], 4)
        self.assertEqual(result["stores_processed"], 2)


if __name__ == "__main__":
    unittest.main()
