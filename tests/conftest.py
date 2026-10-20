"""Корень репозитория в sys.path: CI запускает `pytest tests/` (не `python -m pytest`),
и первый же собранный модуль не должен зависеть от того, что путь добавил другой тест."""
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest


@pytest.fixture(autouse=True)
def _reset_sales_orders_cache():
    """Кэш разобранных sales_orders живёт на уровне процесса, а тесты пишут в
    свежие БД напрямую, мимо _upsert_sales_orders (который его сбрасывает)."""
    from backend.services import sales_orders_cache
    sales_orders_cache.clear()
    yield
    sales_orders_cache.clear()
