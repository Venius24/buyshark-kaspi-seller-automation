import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from starlette.datastructures import Headers
from fastapi import HTTPException

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


class TestStoreContextRejectsUnresolvable(unittest.IsolatedAsyncioTestCase):
    """
    Слайс 7: legacy-фоллбек на общую сессию убран целиком — запрос без
    валидного JWT, без активного User или без доступного этому пользователю
    Store должен получать явный 401, а не тихий доступ к общей/чужой сессии.
    """

    async def test_no_auth_header_rejected(self):
        db = AsyncMock()
        with self.assertRaises(HTTPException) as cm:
            await get_store_context(make_request(), db)
        self.assertEqual(cm.exception.status_code, 401)
        db.execute.assert_not_called()

    async def test_malformed_bearer_header_rejected(self):
        db = AsyncMock()
        with self.assertRaises(HTTPException) as cm:
            await get_store_context(make_request(auth_header="NotBearer garbage"), db)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_expired_or_invalid_jwt_rejected(self):
        db = AsyncMock()
        with self.assertRaises(HTTPException) as cm:
            await get_store_context(make_request(auth_header="Bearer not.a.valid.jwt"), db)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_refresh_type_token_rejected(self):
        """An Authorization header carrying a refresh (not access) token must not be
        accepted as identity — mirrors get_current_user's own type check."""
        from backend.security.auth import create_refresh_token
        refresh_tok = create_refresh_token({"sub": "user-1"})
        db = AsyncMock()
        with self.assertRaises(HTTPException) as cm:
            await get_store_context(make_request(auth_header=f"Bearer {refresh_tok}"), db)
        self.assertEqual(cm.exception.status_code, 401)
        db.execute.assert_not_called()

    async def test_valid_jwt_but_user_not_found_rejected(self):
        access_tok = create_access_token({"sub": "ghost-user-id"})
        db = AsyncMock()
        db.execute.return_value = make_scalar_result(None)  # user not found
        with self.assertRaises(HTTPException) as cm:
            await get_store_context(make_request(auth_header=f"Bearer {access_tok}"), db)
        self.assertEqual(cm.exception.status_code, 401)

    async def test_valid_jwt_but_no_accessible_store_rejected(self):
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)

        db = AsyncMock()
        db.execute.side_effect = [make_scalar_result(user), make_scalar_result(None)]
        with self.assertRaises(HTTPException) as cm:
            await get_store_context(make_request(auth_header=f"Bearer {access_tok}"), db)
        self.assertEqual(cm.exception.status_code, 401)


class TestStoreContextNewPath(unittest.IsolatedAsyncioTestCase):
    """Valid JWT + resolvable store — the per-store path (Phase 4b)."""

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

    @patch("backend.security.store_context.decrypt_data")
    async def test_store_with_undecryptable_key_rejected(self, mock_decrypt):
        """Characterizes the ENCRYPTION_KEY-rotation failure mode: if a store's
        encrypted_api_key can't be decrypted (e.g. stale key), the request must
        fail closed (401), not silently fall back to a shared/legacy session."""
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-1", merchant_id="m_123", name="Shop", encrypted_api_key="cipher")
        mock_decrypt.return_value = ""  # decrypt failure returns "" per crypto.py's contract

        db = AsyncMock()
        db.execute.side_effect = [make_scalar_result(user), make_scalar_result(store)]
        with self.assertRaises(HTTPException) as cm:
            await get_store_context(make_request(auth_header=f"Bearer {access_tok}"), db)
        self.assertEqual(cm.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
