import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient
from jose import jwt

from backend.main import app
from backend.db.database import get_db
from backend.db.models import User
from backend.routers import auth_v1
from backend.security.auth import hash_password, verify_password, SECRET_KEY, ALGORITHM


def make_user(**kw):
    defaults = dict(
        id="u1", email="owner@shop.kz", password_hash=hash_password("oldpass123"),
        is_active=True, is_telegram_only=False, must_change_password=True, is_verified=False,
    )
    defaults.update(kw)
    return User(**defaults)


def scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        auth_v1._forgot_last_by_email.clear()
        auth_v1._forgot_hits_by_ip.clear()
        self.user = make_user()
        self.db = MagicMock()
        self.db.execute = AsyncMock(side_effect=lambda *a, **k: scalar_result(self.user))
        self.db.commit = AsyncMock()

        async def override_db():
            yield self.db

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)

    def test_forgot_sends_email_with_working_link(self):
        with patch.object(auth_v1.mailer, "send_email", new=AsyncMock(return_value=True)) as send:
            resp = self.client.post("/api/v1/auth/forgot-password", json={"email": "Owner@Shop.kz"})
        self.assertEqual(resp.status_code, 200)
        send.assert_awaited_once()
        to, subject, html, text = send.await_args.args
        self.assertEqual(to, "owner@shop.kz")
        self.assertIn("?reset_token=", text)
        token = text.split("?reset_token=")[1].split()[0]

        resp = self.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "newpass456"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(verify_password("newpass456", self.user.password_hash))
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.is_verified)

        # Ссылка одноразовая: после смены пароля тот же токен уже не работает.
        resp = self.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "another789"})
        self.assertEqual(resp.status_code, 400)

    def test_unknown_email_gives_same_answer_and_no_mail(self):
        self.user = None
        with patch.object(auth_v1.mailer, "send_email", new=AsyncMock()) as send:
            resp = self.client.post("/api/v1/auth/forgot-password", json={"email": "nobody@x.kz"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"success": True})
        send.assert_not_awaited()

    def test_telegram_only_user_gets_no_mail(self):
        self.user = make_user(is_telegram_only=True, password_hash="")
        with patch.object(auth_v1.mailer, "send_email", new=AsyncMock()) as send:
            self.client.post("/api/v1/auth/forgot-password", json={"email": "owner@shop.kz"})
        send.assert_not_awaited()

    def test_repeat_request_within_cooldown_is_not_resent(self):
        with patch.object(auth_v1.mailer, "send_email", new=AsyncMock(return_value=True)) as send:
            self.client.post("/api/v1/auth/forgot-password", json={"email": "owner@shop.kz"})
            self.client.post("/api/v1/auth/forgot-password", json={"email": "owner@shop.kz"})
        self.assertEqual(send.await_count, 1)

    def test_expired_token_rejected(self):
        token = jwt.encode(
            {"sub": "u1", "type": "pwd_reset", "pf": auth_v1._password_fingerprint(self.user.password_hash),
             "exp": datetime.utcnow() - timedelta(minutes=1)},
            SECRET_KEY, algorithm=ALGORITHM,
        )
        resp = self.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "newpass456"})
        self.assertEqual(resp.status_code, 400)

    def test_access_token_cannot_be_used_as_reset_token(self):
        from backend.security.auth import create_access_token
        token = create_access_token({"sub": "u1"})
        resp = self.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "newpass456"})
        self.assertEqual(resp.status_code, 400)

    def test_short_password_rejected(self):
        token = auth_v1.create_password_reset_token(self.user)
        resp = self.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "123"})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(verify_password("oldpass123", self.user.password_hash))


class MailerTests(unittest.IsolatedAsyncioTestCase):
    async def test_without_api_key_logs_and_returns_false(self):
        from backend.services import mailer
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RESEND_API_KEY", None)
            self.assertFalse(await mailer.send_email("a@b.kz", "s", "<p>h</p>", "t"))


if __name__ == "__main__":
    unittest.main()
