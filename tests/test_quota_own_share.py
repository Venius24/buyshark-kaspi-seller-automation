"""
Наша доля окна квоты (`KaspiOfferQuota.own_used`).

`used` из limit/settings — счётчик самого Kaspi по ВСЕМУ мерчанту: туда
попадают записи любых интеграций, работающих с тем же кабинетом. Замер на
проде 2026-08-13: в окне, начавшемся в 08:59:20 UTC, Kaspi насчитал 46
записей, тогда как наш флашер за то же окно не записал ни одной. Пока UI
показывал только `used`, чужой расход читался как наш.
"""
import datetime
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from backend.services.kaspi_quota import KaspiOfferQuota, QuotaState


def make_state(used=0, reset_at=None, limit=250):
    return QuotaState(
        limit=limit,
        used=used,
        reset_at=reset_at or datetime.datetime(2026, 8, 13, 14, 29, 20),
        fetched_at=datetime.datetime.now(),
        known=True,
        window_seconds=1800.0,
    )


class TestOwnUsed(unittest.TestCase):
    def setUp(self):
        self.quota = KaspiOfferQuota()

    def test_starts_at_zero_for_unknown_store(self):
        self.assertEqual(self.quota.own_used("store-a"), 0)

    def test_consume_counts_our_writes(self):
        with patch.object(self.quota, "_fetch", return_value=make_state(used=10)):
            self.quota.get("store-a", "20186986")
        self.quota.consume("store-a", 4)
        self.quota.consume("store-a", 3)

        self.assertEqual(self.quota.own_used("store-a"), 7)

    def test_other_integrations_writes_are_not_ours(self):
        """
        Ровно прод-случай: окно выжигает сторонний сервис, мы не пишем ничего.
        Kaspi показывает 46, наша доля обязана остаться нулём.
        """
        with patch.object(self.quota, "_fetch", return_value=make_state(used=46)):
            state = self.quota.get("store-a", "20186986")

        self.assertEqual(state.used, 46)
        self.assertEqual(self.quota.own_used("store-a"), 0)

    def test_new_window_resets_our_count(self):
        first_window = datetime.datetime(2026, 8, 13, 14, 29, 20)
        second_window = datetime.datetime(2026, 8, 13, 14, 59, 20)

        with patch.object(self.quota, "_fetch", return_value=make_state(used=10, reset_at=first_window)):
            self.quota.get("store-a", "20186986")
        self.quota.consume("store-a", 5)
        self.assertEqual(self.quota.own_used("store-a"), 5)

        # Окно Kaspi обнулилось — наш счёт в нём начинается заново.
        with patch.object(self.quota, "_fetch", return_value=make_state(used=0, reset_at=second_window)):
            self.quota.get("store-a", "20186986", force=True)

        self.assertEqual(self.quota.own_used("store-a"), 0)

    def test_same_window_refetch_keeps_our_count(self):
        window = datetime.datetime(2026, 8, 13, 14, 29, 20)
        with patch.object(self.quota, "_fetch", return_value=make_state(used=10, reset_at=window)):
            self.quota.get("store-a", "20186986")
            self.quota.consume("store-a", 6)
            self.quota.get("store-a", "20186986", force=True)

        self.assertEqual(self.quota.own_used("store-a"), 6)

    def test_counts_are_per_store(self):
        with patch.object(self.quota, "_fetch", return_value=make_state()):
            self.quota.get("store-a", "20186986")
            self.quota.get("store-b", "40000001")
        self.quota.consume("store-a", 3)

        self.assertEqual(self.quota.own_used("store-a"), 3)
        self.assertEqual(self.quota.own_used("store-b"), 0)

    def test_consume_before_any_fetch_still_counted(self):
        """Флашер зовёт consume() сразу после отправки батча — терять нельзя."""
        self.quota.consume("store-a", 2)
        self.assertEqual(self.quota.own_used("store-a"), 2)


if __name__ == "__main__":
    unittest.main()
