import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.db.models import Store, User, UserStore
from backend.routers.auth_v1 import get_current_user
from backend.db.database import get_db


class TestTelegramLinkRouter(unittest.TestCase):
    """
    Роутер кабинета для привязки Telegram (docs/TELEGRAM_BOT_PLAN.md, Фаза 1):
    выпуск токена привязки владельцу, приглашение сотрудника, список/отвязка
    привязанных аккаунтов — всё scoped на выбранный (X-Store-Id/старейший)
    магазин пользователя.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        session.add(Store(id="store-1", merchant_id="m_store1", name="TestShop"))
        session.add(User(id="user-1", email="owner@example.com", password_hash="x", is_active=True))
        session.add(UserStore(user_id="user-1", store_id="store-1", role="OWNER"))

        session.add(Store(id="store-2", merchant_id="m_store2", name="OtherShop"))
        session.add(User(id="user-2", email="manager@example.com", password_hash="x", is_active=True))
        session.add(UserStore(user_id="user-2", store_id="store-1", role="MANAGER"))
        session.commit()
        session.close()

        # backend.db.database.engine binds to a RELATIVE sqlite path resolved
        # once at module-import time (before os.chdir above) — the router
        # under test uses Depends(get_db) (async), while the seed data above
        # went through _get_sync_db_session() (resolves the relative path
        # fresh on every call, per its own docstring). Those two point at
        # different files unless get_db is overridden to a fresh async engine
        # bound to THIS test's actual db file (same quirk test_stores_v1_no_fallback.py
        # works around by mocking AsyncSession outright).
        import glob
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        db_files = glob.glob(os.path.join(self._tmp_dir, "*.db"))
        assert db_files, "sync session did not create a sqlite file"
        db_path = db_files[0].replace(os.sep, "/")
        self._async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        self._AsyncSessionLocal = async_sessionmaker(bind=self._async_engine, expire_on_commit=False)

        async def _fake_get_db():
            async with self._AsyncSessionLocal() as s:
                yield s

        self.client = TestClient(app)
        self._fake_user = None
        app.dependency_overrides[get_current_user] = lambda: self._fake_user
        app.dependency_overrides[get_db] = _fake_get_db

    def tearDown(self):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _as(self, user_id: str):
        from backend.state_helpers import _get_sync_db_session
        session = _get_sync_db_session()
        user = session.get(User, user_id)
        session.close()
        self._fake_user = user

    def test_owner_can_create_self_link_token(self):
        self._as("user-1")
        resp = self.client.post("/api/v1/telegram/link-token")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["token"])

    def test_owner_can_create_invite_token(self):
        self._as("user-1")
        resp = self.client.post("/api/v1/telegram/invite-token", json={"role": "MANAGER", "max_uses": 1})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["token"])

    def test_manager_cannot_create_invite_token(self):
        self._as("user-2")
        resp = self.client.post("/api/v1/telegram/invite-token", json={"role": "MANAGER", "max_uses": 1})
        self.assertEqual(resp.status_code, 403)

    def test_invite_token_rejects_owner_role(self):
        self._as("user-1")
        resp = self.client.post("/api/v1/telegram/invite-token", json={"role": "OWNER", "max_uses": 1})
        self.assertEqual(resp.status_code, 400)

    def test_members_list_scoped_to_store(self):
        self._as("user-1")

        from backend.chatbot import telegram_identity as ti
        token = ti.generate_link_token(store_id="store-1", created_by="user-1", invite_role="VIEWER")
        ti.consume_link_token(token, tg_user_id=42, tg_username="seller", tg_first_name="Продавец")

        resp = self.client.get("/api/v1/telegram/members")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["your_role"], "OWNER")
        tg_ids = [m["tg_user_id"] for m in data["members"]]
        self.assertIn(42, tg_ids)

    def test_owner_can_remove_member(self):
        self._as("user-1")

        from backend.chatbot import telegram_identity as ti
        token = ti.generate_link_token(store_id="store-1", created_by="user-1", invite_role="VIEWER")
        ti.consume_link_token(token, tg_user_id=42, tg_username=None, tg_first_name=None)

        resp = self.client.delete("/api/v1/telegram/members/42")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(ti.get_link(42))

    def test_manager_cannot_remove_member(self):
        self._as("user-1")
        from backend.chatbot import telegram_identity as ti
        token = ti.generate_link_token(store_id="store-1", created_by="user-1", invite_role="VIEWER")
        ti.consume_link_token(token, tg_user_id=42, tg_username=None, tg_first_name=None)

        self._as("user-2")
        resp = self.client.delete("/api/v1/telegram/members/42")
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
