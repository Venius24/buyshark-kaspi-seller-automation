import os
import sys
import unittest

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app


class TestOrdersRouterRequiresAuth(unittest.TestCase):
    """
    Слайс 7: legacy-фоллбек на общую сессию убран целиком — запрос без
    валидного JWT+магазина получает 401, а не тихий доступ к общей сессии.
    """

    def setUp(self):
        self.client = TestClient(app)

    def test_get_cities_with_no_auth_header_returns_401(self):
        resp = self.client.get("/api/cities")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
