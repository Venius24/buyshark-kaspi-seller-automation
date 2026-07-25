import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

sys.path.append(os.getcwd())

from backend.main import app
from backend.services.stock_sync_worker import (
    _calculate_physical_units,
    process_packaging_auto_deduction
)

class TestAutoDeductionEngine(unittest.TestCase):
    """
    Unit-тесты автоматического списания остатков Мастер-Групп при обнаружении заказов в Упаковке.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.fake_token = "REDACTED_KASPI_API_TOKEN"

    def test_calculate_physical_units(self):
        """Тест вычисления физических единиц списания для обычных SKU и мульти-паков."""
        self.assertEqual(_calculate_physical_units("T219-0005", 1), 1)
        self.assertEqual(_calculate_physical_units("T219-0005-1", 1), 1)
        self.assertEqual(_calculate_physical_units("T219-0005-2", 1), 2)
        self.assertEqual(_calculate_physical_units("T219-0005-3", 1), 3)
        self.assertEqual(_calculate_physical_units("T219-0005-4", 1), 4)
        self.assertEqual(_calculate_physical_units("T219-0005-6", 1), 6)
        self.assertEqual(_calculate_physical_units("S517-0076", 2), 2)

    @patch("backend.services.stock_sync_worker.execute_broadcast_sync")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_all_packaging_tabs")
    @patch("backend.services.stock_sync_worker._load_master_stock_groups")
    @patch("backend.services.stock_sync_worker._save_master_stock_groups")
    @patch("backend.state_helpers._load_processed_packaging_orders")
    @patch("backend.state_helpers._save_processed_packaging_orders")
    def test_process_packaging_auto_deduction(
        self,
        mock_save_proc,
        mock_load_proc,
        mock_save_groups,
        mock_load_groups,
        mock_tabs,
        mock_sync
    ):
        """Тест списания остатка группы при обнаружении нового заказа на мультипак."""
        mock_load_proc.return_value = {}
        mock_load_groups.return_value = {
            "grp_test": {
                "group_id": "grp_test",
                "group_name": "Тестовая Группа T219",
                "shared_stock": 10,
                "linked_skus": ["T219-0005", "131523919"],
                "sync_logs": []
            }
        }
        mock_tabs.return_value = {
            "cargo_assembly": [
                {
                    "code": "1009990001",
                    "items": [
                        {"merchant_sku": "T219-0005-4", "title": "Сумка 4 шт", "quantity": 1}
                    ]
                }
            ]
        }

        import asyncio
        res = asyncio.run(process_packaging_auto_deduction(self.fake_token))
        
        self.assertTrue(res["success"])
        self.assertEqual(res["deducted_orders_count"], 1)
        self.assertEqual(res["deductions"][0]["units"], 4)
        self.assertEqual(res["deductions"][0]["new_stock"], 6)

    @patch("backend.state_helpers._require_token")
    @patch("backend.services.stock_sync_worker.process_packaging_auto_deduction")
    def test_check_packaging_deduction_endpoint(self, mock_deduction, mock_token):
        """Тест API эндпоинта POST /api/stock-groups/check-packaging-deduction."""
        mock_token.return_value = self.fake_token
        mock_deduction.return_value = {
            "success": True,
            "deducted_orders_count": 1,
            "deductions": [{"order_code": "1009990001", "units": 4, "new_stock": 6}]
        }

        response = self.client.post("/api/stock-groups/check-packaging-deduction")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deducted_orders_count"], 1)


if __name__ == "__main__":
    unittest.main()
