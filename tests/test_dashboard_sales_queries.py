import os
import sys
import json
import shutil
import tempfile
import unittest
import datetime as _dt
from unittest.mock import patch

sys.path.append(os.getcwd())


class DashboardSalesQueriesTest(unittest.TestCase):
    """Виджеты дашборда (score/insights/abc-xyz) читают из sales_orders только
    нужные столбцы, без ORM-объектов (у Бархана ~48k заказов за 180 дней,
    гидратация занимала секунды на каждый опрос). Проверяем, что агрегаты
    остались теми же, что считала полная сводка."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

        import backend.db.models  # noqa: F401
        from backend.db.database import Base
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        db_path = os.path.join(self._tmp_dir, "dashboard_sales_test.db")
        self._engine = create_engine(f"sqlite:///{db_path.replace(os.sep, '/')}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self._engine)

        from backend import state_helpers
        from backend.routers import analytics
        self.state_helpers = state_helpers
        self.analytics = analytics
        analytics._abc_xyz_cache.clear()
        factory = lambda: Session(self._engine)  # noqa: E731
        self._patches = [
            patch.object(state_helpers, "_get_sync_db_session", side_effect=factory),
            patch.object(analytics, "_get_sync_db_session", side_effect=factory),
        ]
        for p in self._patches:
            p.start()

        from backend.db.models import Store, SalesOrderDB
        now = _dt.datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
        session = Session(self._engine)
        session.add(Store(id="store-a", merchant_id="m_a", name="Магазин А"))
        orders = [
            (0, 1000.0, [{"sku": "A", "name": "Товар A", "quantity": 1, "totalPrice": 1000}]),
            (0, 500.0, [{"sku": "B", "name": "Товар B", "quantity": 2, "totalPrice": 500}]),
            (1, 2000.0, [{"sku": "A", "name": "Товар A", "quantity": 2, "totalPrice": 2000}]),
            (40, 300.0, [{"sku": "C", "name": "Товар C", "quantity": 1, "totalPrice": 300}]),
            (200, 9999.0, []),  # за пределами 180 дней
        ]
        for i, (days_ago, price, entries) in enumerate(orders):
            session.add(SalesOrderDB(
                store_id="store-a",
                order_internal_id=f"int-{i}",
                order_code=f"code-{i}",
                created_at=now - _dt.timedelta(days=days_ago),
                total_price=price,
                status="COMPLETED",
                state="ARCHIVE",
                entries_payload=json.dumps(entries),
            ))
        session.commit()
        session.close()
        self.now = now

    def tearDown(self):
        self.analytics._abc_xyz_cache.clear()
        for p in self._patches:
            p.stop()
        self._engine.dispose()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_sales_daily_matches_full_summary(self):
        daily = self.state_helpers._load_sales_daily("store-a", days=180)
        with patch.object(self.state_helpers, "_load_products", return_value=[]):
            summary = self.state_helpers._load_sales_summary("store-a", days=180)
        self.assertEqual(daily, summary["daily"])
        self.assertEqual([d["orders"] for d in daily], [1, 1, 2])
        self.assertEqual(daily[-1]["revenue"], 1500.0)
        self.assertEqual(summary["total_orders"], 4)
        self.assertEqual(summary["top_skus"][0]["sku"], "A")

    def test_insights_heatmap_counts_orders_in_almaty_time(self):
        class Ctx:
            store_id = "store-a"
        with patch.object(self.analytics, "_load_products", return_value=[]), \
             patch.object(self.analytics, "_load_dumping_configs", return_value={}), \
             patch.object(self.analytics, "_load_competitor_leaderboard", return_value=[]), \
             patch.object(self.analytics, "_load_chat_activity", return_value=[]):
            result = self.analytics.get_insights(Ctx())
        grid = result["hourly_heatmap"]
        self.assertEqual(sum(sum(row) for row in grid), 4)  # 90 дней: заказ 200-дневной давности не входит
        local_today = self.now + _dt.timedelta(hours=self.analytics.ALMATY_UTC_OFFSET_HOURS)
        self.assertEqual(grid[local_today.weekday()][local_today.hour], 2)

    def test_abc_xyz_reads_entries(self):
        class Ctx:
            store_id = "store-a"
        with patch.object(self.analytics, "_load_products", return_value=[]):
            result = self.analytics.get_abc_xyz_analysis(90, Ctx())
        by_sku = {it["sku"]: it for it in result["items"]}
        self.assertEqual(by_sku["A"]["revenue"], 3000.0)
        self.assertEqual(by_sku["A"]["quantity"], 3)
        self.assertEqual(result["total_revenue"], 3800.0)

    def test_abc_dominant_sku_is_class_a(self):
        """Один SKU с 85% выручки — класс A, а не B."""
        from sqlalchemy.orm import Session
        from backend.db.models import SalesOrderDB
        session = Session(self._engine)
        for i, (sku, price) in enumerate([("X", 8500.0), ("Y", 1000.0), ("Z", 500.0)]):
            session.add(SalesOrderDB(
                store_id="store-dom",
                order_internal_id=f"dom-{i}",
                order_code=f"dom-code-{i}",
                created_at=self.now,
                total_price=price,
                status="COMPLETED",
                state="ARCHIVE",
                entries_payload=json.dumps([{"sku": sku, "name": sku, "quantity": 1, "totalPrice": price}]),
            ))
        session.commit()
        session.close()

        class Ctx:
            store_id = "store-dom"
        with patch.object(self.analytics, "_load_products", return_value=[]):
            result = self.analytics.get_abc_xyz_analysis(90, Ctx())
        classes = {it["sku"]: it["abc_class"] for it in result["items"]}
        self.assertEqual(classes, {"X": "A", "Y": "B", "Z": "C"})

    def test_abc_xyz_by_master_group_merges_members(self):
        class Ctx:
            store_id = "store-a"
        groups = {
            "g1": {"group_id": "g1", "group_name": "Группа AB", "is_active": True, "linked_skus": ["A", "B"]},
            "g2": {"group_id": "g2", "group_name": "Выключенная", "is_active": False, "linked_skus": ["C"]},
        }
        with patch.object(self.analytics, "_load_products", return_value=[]),              patch.object(self.analytics, "_load_master_stock_groups", return_value=groups):
            result = self.analytics.get_abc_xyz_analysis(90, Ctx(), group_by="group")
        by_key = {it["sku"]: it for it in result["items"]}
        self.assertEqual(set(by_key), {"g1", "C"})  # неактивная группа артикул не забирает
        self.assertEqual(by_key["g1"]["kind"], "group")
        self.assertEqual(by_key["g1"]["name"], "Группа AB")
        self.assertEqual(by_key["g1"]["members_count"], 2)
        self.assertEqual(by_key["g1"]["revenue"], 3500.0)
        self.assertEqual(by_key["g1"]["quantity"], 5)
        self.assertEqual(by_key["C"]["kind"], "sku")
        self.assertEqual(result["total_revenue"], 3800.0)
        self.assertEqual(result["group_by"], "group")

    def test_abc_xyz_group_mode_is_cached_separately_from_sku_mode(self):
        class Ctx:
            store_id = "store-a"
        groups = {"g1": {"group_id": "g1", "group_name": "Г", "is_active": True, "linked_skus": ["A", "B"]}}
        with patch.object(self.analytics, "_load_products", return_value=[]),              patch.object(self.analytics, "_load_master_stock_groups", return_value=groups):
            by_sku = self.analytics.get_abc_xyz_analysis(90, Ctx())
            by_group = self.analytics.get_abc_xyz_analysis(90, Ctx(), group_by="group")
        self.assertEqual(len(by_sku["items"]), 3)
        self.assertEqual(len(by_group["items"]), 2)

    def test_abc_xyz_rejects_unknown_group_by(self):
        from fastapi import HTTPException
        class Ctx:
            store_id = "store-a"
        with self.assertRaises(HTTPException):
            self.analytics.get_abc_xyz_analysis(90, Ctx(), group_by="brand")

    def test_abc_xyz_cache_is_scoped_and_expires(self):
        class Ctx:
            def __init__(self, store_id):
                self.store_id = store_id

        with patch.object(self.analytics, "_load_products", return_value=[]), \
             patch.object(self.analytics.time, "monotonic", return_value=1000) as clock:
            first = self.analytics.get_abc_xyz_analysis(90, Ctx("store-a"))
            self.assertEqual(first["total_revenue"], 3800.0)
            self.assertIs(first, self.analytics.get_abc_xyz_analysis(90, Ctx("store-a")))
            self.assertEqual(self.analytics._get_sync_db_session.call_count, 1)

            self.analytics.get_abc_xyz_analysis(60, Ctx("store-a"))
            self.analytics.get_abc_xyz_analysis(90, Ctx("store-b"))
            self.assertEqual(self.analytics._get_sync_db_session.call_count, 3)

            clock.return_value = 1000 + self.analytics._ABC_XYZ_CACHE_TTL_SECONDS
            self.analytics.get_abc_xyz_analysis(90, Ctx("store-a"))
            self.assertEqual(self.analytics._get_sync_db_session.call_count, 4)


if __name__ == "__main__":
    unittest.main()
