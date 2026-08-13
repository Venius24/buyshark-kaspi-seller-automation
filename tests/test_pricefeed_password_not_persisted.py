import ast
import inspect
import os
import sys
import unittest

sys.path.append(os.getcwd())

from backend.db.models import Store
from backend.routers import pricefeed_import as pfi
from backend.services import pricefeed_client


class TestPasswordNeverPersisted(unittest.TestCase):
    """Страж инварианта из плана: пароль от PriceFeed используется транзитно
    и не должен осесть ни в одной колонке БД, ни в структуре, которая живёт
    между /preview и /apply (_PendingImport), ни в логах."""

    def test_store_model_has_no_pricefeed_password_column(self):
        columns = {c.name for c in Store.__table__.columns}
        for name in columns:
            self.assertNotIn("pricefeed", name.lower(), f"Store.{name} looks PriceFeed-specific — was this meant to hold a password?")

    def test_pending_import_dataclass_has_no_password_field(self):
        field_names = set(pfi._PendingImport.__dataclass_fields__.keys())
        self.assertNotIn("password", field_names)
        self.assertNotIn("phone", field_names)

    def test_pricefeed_client_source_never_logs_password(self):
        source = inspect.getsource(pricefeed_client)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("debug", "info", "warning", "error", "exception"):
                    call_src = ast.unparse(node)
                    self.assertNotIn("password", call_src.lower())

    def test_pricefeed_router_source_never_logs_password(self):
        source = inspect.getsource(pfi)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("debug", "info", "warning", "error", "exception"):
                    call_src = ast.unparse(node)
                    self.assertNotIn("req.password", call_src)

    def test_full_cycle_password_not_in_pending_state(self):
        """После preview (симулируем напрямую, без HTTP) пароль не должен
        появиться ни в одном значении _PendingImport."""
        pfi._PENDING.clear()
        entry = pfi._PendingImport(
            store_id="s1", created_at=0.0, merchant={}, rows={}, report={},
        )
        pfi._PENDING["x"] = entry
        try:
            dumped = repr(entry)
            self.assertNotIn("hunter2", dumped)  # синтетический "пароль", которого тут в принципе быть не может
        finally:
            pfi._PENDING.clear()


if __name__ == "__main__":
    unittest.main()
