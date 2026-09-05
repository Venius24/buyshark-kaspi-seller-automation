"""
Магазин со статусом PAUSED снят с обслуживания оператором: его не берут
фоновые воркеры (list_active_stores фильтрует ровно по этому полю). Проблема
была в том, что пауза не держалась — статус переписывался обратно в 'ACTIVE'
на любом обновлении MC-сессии, то есть один вход владельца в кабинет молча
возвращал магазин во все восемь воркеров, и заметить это можно было только
по логам чужой синхронизации.

Здесь проверяется, что пауза липкая, что данные магазина при этом всё равно
сохраняются (снятие с обслуживания — не потеря доступа к своим данным) и что
action-роуты такому магазину отказывают.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.append(os.getcwd())

import pytest
from fastapi import HTTPException

from backend import state_helpers
from backend.security.store_context import StoreContext, get_store_context_billed


class _FakeStore:
    def __init__(self, status):
        self.status = status
        self.encrypted_cookies = None
        self.last_sync_at = None


def _fake_session_with(store):
    session = MagicMock()
    session.get.return_value = store
    return session


COOKIES = {"mc-session": "s", "mc-sid": "d"}


def test_paused_store_keeps_status_but_still_stores_cookies(monkeypatch):
    store = _FakeStore("PAUSED")
    monkeypatch.setattr(state_helpers, "_get_sync_db_session", lambda: _fake_session_with(store))

    assert state_helpers._save_mc_cookies_for_store("store-x", COOKIES) is True
    # Данные магазина продолжают храниться...
    assert store.encrypted_cookies is not None
    assert store.last_sync_at is not None
    # ...но в обслуживание он не возвращается.
    assert store.status == "PAUSED"


def test_active_store_is_still_marked_active_on_refresh(monkeypatch):
    """Обратная сторона: обычному магазину авто-перевыпуск сессии по-прежнему
    чинит статус после COOKIE_EXPIRED — ради этого строка и написана."""
    store = _FakeStore("COOKIE_EXPIRED")
    monkeypatch.setattr(state_helpers, "_get_sync_db_session", lambda: _fake_session_with(store))

    assert state_helpers._save_mc_cookies_for_store("store-x", COOKIES) is True
    assert store.status == "ACTIVE"


def test_action_routes_refuse_paused_store(monkeypatch):
    """GET'ы кабинета такому магазину доступны (get_store_context их не
    трогает), а действия — нет: это и есть «всё, кроме хранения данных»."""
    monkeypatch.setattr(state_helpers, "_get_store_status", lambda store_id: "PAUSED")
    ctx = StoreContext(token="t", merchant_id="m", store_id="store-x")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_store_context_billed(ctx))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "SERVICE_DISABLED"


def test_action_routes_allow_normal_store(monkeypatch):
    monkeypatch.setattr(state_helpers, "_get_store_status", lambda store_id: "ACTIVE")
    monkeypatch.setattr(
        "backend.billing.enforcement.is_service_blocked", lambda store_id: (False, None)
    )
    ctx = StoreContext(token="t", merchant_id="m", store_id="store-x")
    assert asyncio.run(get_store_context_billed(ctx)) is ctx


def test_evening_summaries_skip_paused_stores(monkeypatch):
    """Вечерний телеграм-дайджест — такое же обслуживание, как воркеры."""
    from backend.chatbot import notify_dispatch

    recorded = {}

    class _Query:
        def filter(self, criterion):
            # literal_binds — иначе SQLAlchemy прячет значение за :status_1,
            # и тест не отличит фильтр по PAUSED от любого другого.
            recorded["criterion"] = str(
                criterion.compile(compile_kwargs={"literal_binds": True})
            )
            return self

        def all(self):
            return []

    session = MagicMock()
    session.query.return_value = _Query()
    monkeypatch.setattr(state_helpers, "_get_sync_db_session", lambda: session)

    assert notify_dispatch._all_store_ids() == []
    assert "PAUSED" in recorded["criterion"]
