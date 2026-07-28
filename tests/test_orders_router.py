import os
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.append(os.getcwd())

from backend.main import app

class TestOrdersRouter(unittest.TestCase):
    """
    Тестовый набор для роутера заказов /api/orders и его суб-эндпоинтов.
    """

    def setUp(self):
        self.client = TestClient(app)
        self.fake_token = "REDACTED_KASPI_API_TOKEN"

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_all_packaging_tabs")
    def test_get_packaging_tabs_endpoint(self, mock_tabs, mock_token, mock_merchant_id):
        """Проверка GET /api/orders/packaging/tabs."""
        mock_token.return_value = self.fake_token
        mock_tabs.return_value = {
            "success": True,
            "cargo_assembly": [{"id": "1001", "code": "1009566143"}],
            "wait_for_courier": [],
            "transmitted": [],
            "counts": {"cargo_assembly": 1, "wait_for_courier": 0, "transmitted": 0}
        }

        response = self.client.get("/api/orders/packaging/tabs")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["counts"]["cargo_assembly"], 1)

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_packaging_orders_by_sku_prefix")
    def test_get_packaging_by_prefix_endpoint(self, mock_prefix, mock_token, mock_merchant_id):
        """Проверка GET /api/orders/packaging/by-prefix."""
        mock_token.return_value = self.fake_token
        mock_prefix.return_value = {
            "success": True,
            "prefix": "T219-0005",
            "count": 2,
            "total_quantity": 3,
            "orders": [{"id": "1001"}, {"id": "1002"}]
        }

        response = self.client.get("/api/orders/packaging/by-prefix?prefix=T219-0005")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["prefix"], "T219-0005")
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["total_quantity"], 3)

    @patch("backend.security.store_context.discover_merchant_id")
    @patch("backend.security.store_context._require_token")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_orders_with_products")
    def test_get_orders_endpoint(self, mock_orders, mock_token, mock_merchant_id):
        """Проверка GET /api/orders."""
        mock_token.return_value = self.fake_token
        mock_orders.return_value = [
            {"id": "1009566143", "productName": "Грипсы Barkhan", "assembled": False}
        ]

        response = self.client.get("/api/orders?state=KASPI_DELIVERY&status=ACCEPTED_BY_MERCHANT&limit=10&assembled=false")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["orders"]), 1)
        self.assertEqual(data["orders"][0]["id"], "1009566143")


if __name__ == "__main__":
    unittest.main()
