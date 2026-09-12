"""
Алерт о слетевшем входе в кабинет должен уходить владельцам КОНКРЕТНОГО стора.
Раньше health-check звал notify_cookie_expired без store_id, и алерт молча
оседал в логе «некому отправлять» (прод, Q-STEPPE, 03.09.2026).
"""
import asyncio
from unittest.mock import AsyncMock, patch

from backend.chatbot import worker  # noqa: F401 — разрывает цикл импорта notifications↔worker
from backend.chatbot import notifications


def test_cabinet_alert_dispatched_to_store():
    with patch.object(notifications.notify_dispatch, "dispatch", new=AsyncMock()) as dispatch:
        asyncio.run(notifications.notify_cabinet_session_expired("store-42", "Q-STEPPE"))

    dispatch.assert_awaited_once()
    args, kwargs = dispatch.call_args
    assert args[0] == "store-42"
    assert args[1] == "problems"
    assert kwargs.get("urgent") is True
    # Текст про кабинет, а не про SMS-вход в чат.
    assert "КАБИНЕТ" in args[2]
    assert "SMS" not in args[2]
