import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from starlette.datastructures import Headers

from backend.security.store_context import get_store_context
from backend.security.auth import create_access_token
from backend.db.models import User, Store


def make_request(auth_header=None, store_id_header=None):
    raw = {}
    if auth_header:
        raw["Authorization"] = auth_header
    if store_id_header:
        raw["X-Store-Id"] = store_id_header
    req = MagicMock()
    req.headers = Headers(raw)  # real case-insensitive header lookup, matching Starlette at runtime
    return req


def make_scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    scalars_result = MagicMock()
    scalars_result.first.return_value = value
    result.scalars.return_value = scalars_result
    return result


class TestStoreContextLegacyFallback(unittest.IsolatedAsyncioTestCase):
    """No JWT (or an invalid/expired one) — must behave EXACTLY like today's
    _require_token() + discover_merchant_id(), since existing clients don't
    send a JWT to these endpoints at all yet."""

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    async def test_no_auth_header_falls_back_to_legacy(self, mock_require_token, mock_discover):
        mock_require_token.return_value = "legacy_token_123"
        mock_discover.return_value = "20186986"
        db = AsyncMock()
        ctx = await get_store_context(make_request(), db)
        self.assertEqual(ctx.token, "legacy_token_123")
        self.assertEqual(ctx.merchant_id, "20186986")
        self.assertIsNone(ctx.store_id)
        db.execute.assert_not_called()

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    async def test_malformed_bearer_header_falls_back_to_legacy(self, mock_require_token, mock_discover):
        mock_require_token.return_value = "legacy_token"
        mock_discover.return_value = "20186986"
        db = AsyncMock()
        ctx = await get_store_context(make_request(auth_header="NotBearer garbage"), db)
        self.assertIsNone(ctx.store_id)
        self.assertEqual(ctx.token, "legacy_token")

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    async def test_expired_or_invalid_jwt_falls_back_to_legacy(self, mock_require_token, mock_discover):
        mock_require_token.return_value = "legacy_token"
        mock_discover.return_value = "20186986"
        db = AsyncMock()
        ctx = await get_store_context(make_request(auth_header="Bearer not.a.valid.jwt"), db)
        self.assertIsNone(ctx.store_id)

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    async def test_refresh_type_token_rejected_falls_back_to_legacy(self, mock_require_token, mock_discover):
        """An Authorization header carrying a refresh (not access) token must not be
        accepted as identity — mirrors get_current_user's own type check."""
        mock_require_token.return_value = "legacy_token"
        mock_discover.return_value = "20186986"
        from backend.security.auth import create_refresh_token
        refresh_tok = create_refresh_token({"sub": "user-1"})
        db = AsyncMock()
        ctx = await get_store_context(make_request(auth_header=f"Bearer {refresh_tok}"), db)
        self.assertIsNone(ctx.store_id)
        db.execute.assert_not_called()

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    async def test_valid_jwt_but_user_not_found_falls_back_to_legacy(self, mock_require_token, mock_discover):
        mock_require_token.return_value = "legacy_token"
        mock_discover.return_value = "20186986"
        access_tok = create_access_token({"sub": "ghost-user-id"})
        db = AsyncMock()
        db.execute.return_value = make_scalar_result(None)  # user not found
        ctx = await get_store_context(make_request(auth_header=f"Bearer {access_tok}"), db)
        self.assertIsNone(ctx.store_id)
        self.assertEqual(ctx.token, "legacy_token")

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    async def test_valid_jwt_but_no_accessible_store_falls_back_to_legacy(self, mock_require_token, mock_discover):
        mock_require_token.return_value = "legacy_token"
        mock_discover.return_value = "20186986"
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)

        db = AsyncMock()
        db.execute.side_effect = [make_scalar_result(user), make_scalar_result(None)]
        ctx = await get_store_context(make_request(auth_header=f"Bearer {access_tok}"), db)
        self.assertIsNone(ctx.store_id)


class TestStoreContextNewPath(unittest.IsolatedAsyncioTestCase):
    """Valid JWT + resolvable store — the new per-store path (Phase 4b)."""

    @patch("backend.security.store_context.decrypt_data")
    async def test_valid_jwt_resolves_store_by_default_oldest(self, mock_decrypt):
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-1", merchant_id="m_123", name="Shop", encrypted_api_key="cipher")
        mock_decrypt.return_value = "plaintext_token"

        db = AsyncMock()
        db.execute.side_effect = [make_scalar_result(user), make_scalar_result(store)]
        ctx = await get_store_context(make_request(auth_header=f"Bearer {access_tok}"), db)

        self.assertEqual(ctx.store_id, "store-1")
        self.assertEqual(ctx.merchant_id, "m_123")
        self.assertEqual(ctx.token, "plaintext_token")

    @patch("backend.security.store_context.decrypt_data")
    async def test_explicit_store_id_header_is_respected(self, mock_decrypt):
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-2", merchant_id="m_456", name="Shop2", encrypted_api_key="cipher2")
        mock_decrypt.return_value = "plaintext_token_2"

        db = AsyncMock()
        db.execute.side_effect = [make_scalar_result(user), make_scalar_result(store)]
        ctx = await get_store_context(
            make_request(auth_header=f"Bearer {access_tok}", store_id_header="store-2"), db
        )
        self.assertEqual(ctx.store_id, "store-2")
        self.assertEqual(ctx.merchant_id, "m_456")

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    @patch("backend.security.store_context.decrypt_data")
    async def test_store_with_undecryptable_key_falls_back_to_legacy(
        self, mock_decrypt, mock_require_token, mock_discover
    ):
        """Characterizes the ENCRYPTION_KEY-rotation failure mode: if a store's
        encrypted_api_key can't be decrypted (e.g. stale key), don't error out —
        fall back to legacy so the request still succeeds."""
        mock_require_token.return_value = "legacy_token"
        mock_discover.return_value = "20186986"
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-1", merchant_id="m_123", name="Shop", encrypted_api_key="cipher")
        mock_decrypt.return_value = ""  # decrypt failure returns "" per crypto.py's contract

        db = AsyncMock()
        db.execute.side_effect = [make_scalar_result(user), make_scalar_result(store)]
        ctx = await get_store_context(make_request(auth_header=f"Bearer {access_tok}"), db)
        self.assertIsNone(ctx.store_id)
        self.assertEqual(ctx.token, "legacy_token")


if __name__ == "__main__":
    unittest.main()
