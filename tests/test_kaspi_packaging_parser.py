import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.append(os.getcwd())

from backend.kaspi_wrapper import KaspiAPI


class TestKaspiPackagingParser(unittest.TestCase):
    """
    Тестовый набор для проверки парсинга Kaspi JSON:API и вкладки Упаковка.
    """

    def setUp(self):
        self.fake_token = "MOCK_TOKEN_12345"
        self.mock_json_api_response = {
            "data": [
                {
                    "id": "1001",
                    "type": "orders",
                    "attributes": {
                        "code": "1009566143",
                        "totalPrice": 590.0,
                        "status": "ACCEPTED_BY_MERCHANT",
                        "state": "KASPI_DELIVERY",
                        "assembled": False,
                        "creationDate": 1784187000000,
                        "customer": {"firstName": "Алексей", "cellPhone": "+77010000001"},
                        "kaspiDelivery": {"courierTransmissionDate": None, "waybill": "WB-001"}
                    },
                    "relationships": {
                        "entries": {
                            "data": [{"type": "orderentries", "id": "ENTRY_001"}]
                        }
                    }
                },
                {
                    "id": "1002",
                    "type": "orders",
                    "attributes": {
                        "code": "1009572013",
                        "totalPrice": 3355.0,
                        "status": "ACCEPTED_BY_MERCHANT",
                        "state": "KASPI_DELIVERY",
                        "assembled": True,
                        "creationDate": 1784187500000,
                        "customer": {"name": "Anna", "cellPhone": "+77010000002"},
                        "kaspiDelivery": {"courierTransmissionDate": None, "waybill": "WB-002"}
                    },
                    "relationships": {
                        "entries": {
                            "data": [{"type": "orderentries", "id": "ENTRY_002"}]
                        }
                    }
                },
                {
                    "id": "1003",
                    "type": "orders",
                    "attributes": {
                        "code": "1009941766",
                        "totalPrice": 1200.0,
                        "status": "ACCEPTED_BY_MERCHANT",
                        "state": "KASPI_DELIVERY",
                        "assembled": True,
                        "creationDate": 1784188000000,
                        "customer": {"name": "Бауржан"},
                        "kaspiDelivery": {"courierTransmissionDate": 1784190000000, "waybill": "WB-003"}
                    },
                    "relationships": {
                        "entries": {
                            "data": [{"type": "orderentries", "id": "ENTRY_003"}]
                        }
                    }
                },
                {
                    "id": "1004",
                    "type": "orders",
                    "attributes": {
                        "code": "1009945000",
                        "totalPrice": 4500.0,
                        "status": "ACCEPTED_BY_MERCHANT",
                        "state": "KASPI_DELIVERY",
                        "assembled": False,
                        "creationDate": 1784188500000,
                        "customer": {"firstName": "Даурен"},
                        "kaspiDelivery": {"courierTransmissionDate": None}
                    },
                    "relationships": {
                        "entries": {
                            "data": [{"type": "orderentries", "id": "ENTRY_004"}]
                        }
                    }
                }
            ],
            "included": [
                {
                    "id": "ENTRY_001",
                    "type": "orderentries",
                    "attributes": {
                        "quantity": 1,
                        "basePrice": 590.0,
                        "offer": {
                            "code": "S517-0076",
                            "name": "Грипсы Barkhan S517-0076 22 12 см"
                        }
                    }
                },
                {
                    "id": "ENTRY_002",
                    "type": "orderentries",
                    "attributes": {
                        "quantity": 2,
                        "basePrice": 1677.5,
                        "offer": {
                            "code": "T219-0005-2",
                            "title": "Термосумка Barkhan T219-0005 2 шт"
                        }
                    }
                },
                {
                    "id": "ENTRY_003",
                    "type": "orderentries",
                    "attributes": {
                        "quantity": 1,
                        "basePrice": 1200.0,
                        "offer": {
                            "code": "T219-0005-4",
                            "name": "Термосумка Barkhan T219-0005 4 шт"
                        }
                    }
                },
                {
                    "id": "ENTRY_004",
                    "type": "orderentries",
                    "attributes": {
                        "quantity": 1,
                        "basePrice": 4500.0,
                        "offer": {
                            "code": "T219-0005",
                            "name": "Термосумка Barkhan T219-0005 Базовая"
                        }
                    }
                }
            ]
        }

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_get_orders_with_products_assembled_false(self, mock_get):
        """Тест выгрузки заказов 'На сборке' (assembled=False)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_json_api_response
        mock_get.return_value = mock_resp

        orders = KaspiAPI.get_orders_with_products(self.fake_token, limit=10, assembled=False)

        # Ожидаем 2 заказа: #1009566143 и #1009945000 (у которых assembled=False)
        self.assertEqual(len(orders), 2)
        codes = [o["id"] for o in orders]
        self.assertIn("1009566143", codes)
        self.assertIn("1009945000", codes)
        
        # Проверяем товары и артикулы
        order_1 = next(o for o in orders if o["id"] == "1009566143")
        self.assertEqual(order_1["entries"][0]["merchantSku"], "S517-0076")
        self.assertEqual(order_1["entries"][0]["productName"], "Грипсы Barkhan S517-0076 22 12 см")

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_get_all_packaging_tabs(self, mock_get):
        """Тест правильной раскладки по 3 вкладкам: cargo_assembly, wait_for_courier, transmitted."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_json_api_response
        mock_get.return_value = mock_resp

        res = KaspiAPI.get_all_packaging_tabs(self.fake_token)

        self.assertTrue(res["success"])
        self.assertEqual(res["counts"]["cargo_assembly"], 2)
        self.assertEqual(res["counts"]["wait_for_courier"], 1)
        self.assertEqual(res["counts"]["transmitted"], 1)

        # 1. Cargo assembly: 1009566143, 1009945000
        ca_codes = [o["code"] for o in res["cargo_assembly"]]
        self.assertListEqual(sorted(ca_codes), ["1009566143", "1009945000"])

        # 2. Wait for courier: 1009572013
        wfc_codes = [o["code"] for o in res["wait_for_courier"]]
        self.assertListEqual(wfc_codes, ["1009572013"])

        # 3. Transmitted: 1009941766
        tr_codes = [o["code"] for o in res["transmitted"]]
        self.assertListEqual(tr_codes, ["1009941766"])

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_get_packaging_orders_by_sku_prefix_dash_variants(self, mock_get):
        """Тест поиска заказов по префиксу артикула с вариантами тире (T219-0005, T219-0005-2...)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.mock_json_api_response
        mock_get.return_value = mock_resp

        # Запрашиваем по префиксу "T219-0005"
        res = KaspiAPI.get_packaging_orders_by_sku_prefix(self.fake_token, "T219-0005")

        self.assertTrue(res["success"])
        # Из unassembled заказов (1009566143 и 1009945000):
        # 1009945000 содержит T219-0005.
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["orders"][0]["id"], "1009945000")

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_product_title_fallback(self, mock_get):
        """Тест фолбека offer.get('name') or offer.get('title')."""
        custom_response = {
            "data": [{
                "id": "2001",
                "attributes": {"code": "2009566143", "totalPrice": 1000.0, "assembled": False},
                "relationships": {"entries": {"data": [{"id": "E_TITLE_ONLY"}]}}
            }],
            "included": [{
                "id": "E_TITLE_ONLY",
                "type": "orderentries",
                "attributes": {
                    "quantity": 1,
                    "basePrice": 1000.0,
                    "offer": {"code": "TITLE_SKU", "title": "Товар из поля Title"}
                }
            }]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = custom_response
        mock_get.return_value = mock_resp

        orders = KaspiAPI.get_orders_with_products(self.fake_token, assembled=False)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["entries"][0]["productName"], "Товар из поля Title")


if __name__ == "__main__":
    unittest.main()
