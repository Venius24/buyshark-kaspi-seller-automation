import os
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.append(os.getcwd())

from backend.main import app
from backend.kaspi_wrapper import KaspiAPI

class TestStockGroupsPackaging(unittest.TestCase):
    """
    Тесты интеграции поиска заказов в 'Упаковке' для Мастер-Групп.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.fake_token = "REDACTED_KASPI_API_TOKEN"

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_get_packaging_orders_for_skus(self, mock_get):
        """Тест метода KaspiAPI.get_packaging_orders_for_skus."""
        mock_response = {
            "data": [{
                "id": "1001",
                "attributes": {"code": "1010739224", "totalPrice": 690.0, "assembled": False},
                "relationships": {"entries": {"data": [{"id": "E1"}]}}
            }],
            "included": [{
                "id": "E1",
                "type": "orderentries",
                "attributes": {
                    "quantity": 1,
                    "basePrice": 690.0,
                    "offer": {"code": "T219-0005-2", "name": "Термосумка 3 л"}
                }
            }]
        }
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_response

        skus = ["T219-0005", "131523919"]
        res = KaspiAPI.get_packaging_orders_for_skus(self.fake_token, skus)
        
        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["total_quantity"], 1)
        self.assertEqual(res["orders"][0]["entries"][0]["merchantSku"], "T219-0005-2")

    @patch("backend.routers.stock_groups._load_master_stock_groups")
    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_packaging_orders_for_skus")
    def test_get_stock_group_packaging_endpoint(self, mock_packaging_skus, mock_token, mock_merchant_id, mock_load_groups):
        """Тест GET /api/stock-groups/{group_id}/packaging."""
        mock_token.return_value = self.fake_token
        mock_load_groups.return_value = {
            "grp_9662bbb1": {"group_id": "grp_9662bbb1", "group_name": "сумка t219-0005", "linked_skus": ["T219-0005"]}
        }
        mock_packaging_skus.return_value = {
            "success": True,
            "count": 6,
            "total_quantity": 6,
            "orders": [{"id": "1010720302"}, {"id": "1010739224"}]
        }

        response = self.client.get("/api/stock-groups/grp_9662bbb1/packaging")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["group_id"], "grp_9662bbb1")
        self.assertEqual(data["count"], 6)
        self.assertEqual(data["total_quantity"], 6)


if __name__ == "__main__":
    unittest.main()
