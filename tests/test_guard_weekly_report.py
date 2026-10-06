import datetime as dt
import os
import sys
import unittest

sys.path.append(os.getcwd())

from backend.services import guard_weekly_report as gwr


def order(created, status="COMPLETED", entries=(("A", 1, 100.0),)):
    return {"created": created, "status": status, "total": sum(e[2] for e in entries),
            "entries": [{"sku": s, "qty": q, "total": t} for s, q, t in entries]}


class TestBucket(unittest.TestCase):
    GE = dt.datetime(2026, 10, 4, 19)   # 05.10 00:00 по Алматы
    LE = GE + dt.timedelta(days=7)

    def test_counts_gross_and_net_by_sku_group(self):
        orders = [
            order(self.GE + dt.timedelta(hours=10), entries=(("A", 2, 300.0), ("B", 1, 50.0))),
            order(self.GE + dt.timedelta(hours=11), status="CANCELLED", entries=(("A", 1, 150.0),)),
            order(self.GE - dt.timedelta(hours=1), entries=(("A", 1, 999.0),)),   # до недели
            order(self.GE + dt.timedelta(hours=1), entries=(("B", 1, 70.0),)),
        ]
        a = gwr._bucket(orders, self.GE, self.LE, {"A"})
        self.assertEqual((a["orders"], a["revenue"], a["units"]), (2, 450, 3))
        self.assertEqual((a["orders_net"], a["revenue_net"]), (1, 300))
        self.assertEqual(a["avg_unit_price"], 150)
        ctrl = gwr._bucket(orders, self.GE, self.LE, None, exclude={"A"})
        self.assertEqual((ctrl["orders"], ctrl["revenue"]), (2, 120))

    def test_night_orders_use_local_time(self):
        # 22:30 UTC = 03:30 по Алматы — ночь (3–7); 20:30 UTC = 01:30 — нет.
        orders = [order(dt.datetime(2026, 10, 5, 22, 30)), order(dt.datetime(2026, 10, 5, 20, 30))]
        self.assertEqual(gwr._bucket(orders, self.GE, self.LE, None)["night_orders"], 1)

    def test_cancel_statuses(self):
        for s in ("CANCELLED", "CANCELLING", "KASPI_DELIVERY_RETURN_REQUESTED", "RETURNED"):
            self.assertTrue(gwr._is_cancelled(s))
        for s in ("COMPLETED", "ACCEPTED_BY_MERCHANT", ""):
            self.assertFalse(gwr._is_cancelled(s))


if __name__ == "__main__":
    unittest.main()
