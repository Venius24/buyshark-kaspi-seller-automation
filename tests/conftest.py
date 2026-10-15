"""Корень репозитория в sys.path: CI запускает `pytest tests/` (не `python -m pytest`),
и первый же собранный модуль не должен зависеть от того, что путь добавил другой тест."""
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
