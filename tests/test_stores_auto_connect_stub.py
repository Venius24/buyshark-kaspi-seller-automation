import os
import sys
import unittest

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import stores_auto_connect


class TestAutoConnectStubIsInert(unittest.TestCase):
    """
    Каркас "Сделайте всё за меня" (см. AUTOCONNECT_PLAN.md) не должен
    ничего делать, пока не выставлен AUTO_CONNECT_ENABLED=1 и не
    реализована Фаза 1 — эти тесты фиксируют, что новый роутер безопасно
    примонтирован, но неактивен по умолчанию.
    """

    def setUp(self):
        self.client = TestClient(app)
        os.environ.pop("AUTO_CONNECT_ENABLED", None)

    def test_start_without_auth_returns_401(self):
        resp = self.client.post(
            "/api/v1/stores/auto-connect/start", json={"phone": "+77001234567"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_verify_without_auth_returns_401(self):
        resp = self.client.post(
            "/api/v1/stores/auto-connect/verify",
            json={"phone": "+77001234567", "code": "1234", "store_name": "Test"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_disabled_by_default(self):
        self.assertFalse(stores_auto_connect._auto_connect_enabled())

    def test_enabled_via_env_var(self):
        os.environ["AUTO_CONNECT_ENABLED"] = "1"
        try:
            self.assertTrue(stores_auto_connect._auto_connect_enabled())
        finally:
            os.environ.pop("AUTO_CONNECT_ENABLED", None)


class TestAutoConnectPhase1NotImplemented(unittest.TestCase):
    """create_employee_and_fetch_token — осознанный NotImplementedError,
    пока нет доступа к владельческому кабинету для разметки селекторов
    (см. AUTOCONNECT_PLAN.md, Фаза 1)."""

    def test_create_employee_raises_not_implemented(self):
        from backend.services import kaspi_auto_connect

        with self.assertRaises(NotImplementedError):
            kaspi_auto_connect.create_employee_and_fetch_token({}, "Test Store")


if __name__ == "__main__":
    unittest.main()
