"""getDiffGroups отдаёт диалоги от старых к новым: список чатов и поиск чата
заказа должны листать вперёд, а не брать первую страницу. Неудачный первый
контакт не должен вечно держать голову очереди (HANDOFF §3.110)."""
import os
import sys
import time
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

sys.path.append(os.getcwd())

import backend.chatbot.worker  # noqa: F401 — разрывает циклический импорт kaspi <-> chat_client
from backend.chatbot import chat_client, outbound


def _pages(total: int, page_size: int, start_gts: int):
    """Фейковый getDiffGroups: total диалогов с растущим gts, отдаёт page_size после gts."""
    groups = [{"id": f"g{i}", "gts": start_gts + i, "analytics": {"order_id": str(1000 + i)}} for i in range(total)]

    async def diff_page(cookies, merchant_id, gts, limit=200):
        after = [g for g in groups if g["gts"] > gts]
        page = after[:min(limit, page_size)]
        return {"groups": page, "more": len(after) > len(page), "sts": start_gts + total + 100}

    return diff_page


class ListGroupsTest(unittest.TestCase):
    def test_returns_newest_first_not_oldest_page(self):
        now_ms = int(time.time() * 1000)
        with patch.object(chat_client, "diff_page", side_effect=_pages(500, 200, now_ms - 3600_000)):
            got = asyncio.run(chat_client.list_groups({}, "m", limit=50))
        self.assertEqual(len(got), 50)
        self.assertEqual(got[0]["id"], "g499")
        self.assertEqual(got[-1]["id"], "g450")


class LinkScanTest(unittest.TestCase):
    def setUp(self):
        outbound._link_scan_cursor.clear()
        self.cache = {}
        self._p = [
            patch.object(outbound, "_cache_order_chat_links", side_effect=lambda sid, pairs: self.cache.update(pairs)),
            patch.object(outbound, "_load_cached_group_id", side_effect=lambda sid, oid: self.cache.get(oid)),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        outbound._link_scan_cursor.clear()

    def test_finds_recent_order_beyond_first_page(self):
        now_ms = int(time.time() * 1000)
        with patch.object(chat_client, "diff_page", side_effect=_pages(700, 200, now_ms - 86_400_000)):
            gid = asyncio.run(outbound.resolve_group_id("s", "1699", {}, "m"))
        self.assertEqual(gid, "g699")

    def test_second_scan_starts_from_cursor(self):
        now_ms = int(time.time() * 1000)
        fake = AsyncMock(side_effect=_pages(300, 200, now_ms - 86_400_000))
        with patch.object(chat_client, "diff_page", fake):
            asyncio.run(outbound.sync_order_chat_links("s", {}, "m"))
            calls_first = fake.await_count
            asyncio.run(outbound.sync_order_chat_links("s", {}, "m"))
        self.assertEqual(calls_first, 2)
        self.assertEqual(fake.await_count, 3)
        self.assertEqual(fake.await_args.args[2], now_ms - 86_400_000 + 299)


class FirstContactGiveUpTest(unittest.TestCase):
    def _item(self, attempts):
        import datetime as dt
        return SimpleNamespace(
            id=1, order_id="1001", campaign="review", group_id=None, message_text="Оставьте отзыв",
            scheduled_at=dt.datetime.utcnow(), window_start_hour=None, window_end_hour=None, attempts=attempts,
        )

    def _run(self, attempts):
        marks = []
        with patch.object(outbound, "_load_due_pending", return_value=[self._item(attempts)]), \
             patch.object(outbound, "_sent_today_count", return_value=0), \
             patch.object(outbound, "resolve_group_id", AsyncMock(return_value=None)), \
             patch.object(outbound, "send_first_contact", return_value=(False, "Поле ввода чата не появилось")), \
             patch.object(outbound, "_mark", side_effect=lambda *a, **k: marks.append(a)), \
             patch.object(outbound.asyncio, "sleep", AsyncMock()):
            asyncio.run(outbound._process_store_queue("s", {"mc-session": "x", "mc-sid": "y"}, "m"))
        return marks

    def test_retries_while_attempts_left(self):
        [(item_id, status, reason)] = self._run(attempts=0)
        self.assertEqual(status, "needs_first_contact")

    def test_gives_up_after_max_attempts(self):
        [(item_id, status, reason)] = self._run(attempts=outbound.FIRST_CONTACT_MAX_ATTEMPTS - 1)
        self.assertEqual(status, "failed")
        self.assertIn("Поле ввода", reason)


if __name__ == "__main__":
    unittest.main()
