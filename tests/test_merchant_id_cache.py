"""
Кэш merchant_id (`KaspiAPI.get_merchant_id`).

merchant_id по токену не меняется, а стоит дорого: шаг через
/pointofservices на проде стабильно отдаёт 500, дальше идёт фоллбек через
историю заказов — два запроса к Kaspi на каждый вызов. Зовут его из горячих
мест (луп чатбота, outbound, веер Мастер-Групп, routers/chats.py на каждый
запрос фронта), и в логах прода 2026-08-13 эти 500 шли по 5 раз в минуту.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.getcwd())

from backend.kaspi_wrapper import KaspiAPI


class TestMerchantIdCache(unittest.TestCase):
    def setUp(self):
        with KaspiAPI._merchant_id_cache_lock:
            KaspiAPI._merchant_id_cache.clear()

    tearDown = setUp

    @patch("backend.kaspi_wrapper.KaspiAPI._resolve_merchant_id")
    def test_repeated_calls_resolve_once(self, mock_resolve):
        mock_resolve.return_value = "20186986"

        results = [KaspiAPI.get_merchant_id("token-a") for _ in range(10)]

        self.assertEqual(results, ["20186986"] * 10)
        self.assertEqual(mock_resolve.call_count, 1)

    @patch("backend.kaspi_wrapper.KaspiAPI._resolve_merchant_id")
    def test_cache_is_per_token(self, mock_resolve):
        mock_resolve.side_effect = ["20186986", "40000001"]

        first = KaspiAPI.get_merchant_id("token-a")
        second = KaspiAPI.get_merchant_id("token-b")
        again = KaspiAPI.get_merchant_id("token-a")

        self.assertEqual((first, second, again), ("20186986", "40000001", "20186986"))
        self.assertEqual(mock_resolve.call_count, 2)

    @patch("backend.kaspi_wrapper.KaspiAPI._resolve_merchant_id")
    def test_synthetic_id_expires_quickly_and_is_retried(self, mock_resolve):
        """
        m_<hash> — это «не резолвится», а не ответ: часть кода (offer_sync)
        на него осознанно не полагается. Кэшируем коротко, чтобы погасить
        шторм запросов, но не залипнуть навсегда, если Kaspi оживёт.
        """
        mock_resolve.side_effect = ["m_deadbeef", "20186986"]

        first = KaspiAPI.get_merchant_id("token-a")
        self.assertEqual(first, "m_deadbeef")

        # Протухание отрицательного результата — без sleep в тесте.
        with KaspiAPI._merchant_id_cache_lock:
            KaspiAPI._merchant_id_cache["token-a"]["expires_at"] = 0.0

        self.assertEqual(KaspiAPI.get_merchant_id("token-a"), "20186986")
        self.assertEqual(mock_resolve.call_count, 2)

    @patch("backend.kaspi_wrapper.KaspiAPI._resolve_merchant_id")
    def test_real_id_gets_the_long_ttl(self, mock_resolve):
        mock_resolve.return_value = "20186986"
        KaspiAPI.get_merchant_id("token-a")

        with KaspiAPI._merchant_id_cache_lock:
            entry = KaspiAPI._merchant_id_cache["token-a"]

        import time as _time

        remaining = entry["expires_at"] - _time.time()
        self.assertGreater(remaining, KaspiAPI._MERCHANT_ID_NEGATIVE_TTL_SECONDS)


class TestMerchantIdResolutionStillWorks(unittest.TestCase):
    """Кэш не должен менять сам резолв — проверяем оба его пути."""

    def setUp(self):
        with KaspiAPI._merchant_id_cache_lock:
            KaspiAPI._merchant_id_cache.clear()

    tearDown = setUp

    @patch("backend.kaspi_wrapper.KaspiAPI.get_point_of_services")
    def test_resolves_from_point_of_services(self, mock_pos):
        mock_pos.return_value = [{"id": "20186986_001"}]
        self.assertEqual(KaspiAPI.get_merchant_id("token-a"), "20186986")

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_point_of_services")
    def test_falls_back_to_synthetic_when_everything_fails(self, mock_pos, mock_get):
        mock_pos.side_effect = Exception("500 Server Error")
        mock_get.side_effect = Exception("network down")

        result = KaspiAPI.get_merchant_id("token-a")

        self.assertTrue(result.startswith("m_"))


if __name__ == "__main__":
    unittest.main()
