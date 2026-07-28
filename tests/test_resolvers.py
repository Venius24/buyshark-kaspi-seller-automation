"""
Характеризующие тесты для дублирующихся реализаций merchant-ID резолвера
и cookie-лоадера (dumper/config.py vs kaspi_wrapper.py, state_helpers.py vs
kaspi_wrapper.py) — доказательная база для консолидации в Phase 4c плана
рефакторинга. Тесты фиксируют ТЕКУЩЕЕ (включая явно ошибочное) поведение,
а не желаемое — не менять их логику вне Phase 4c/4e.
"""
import base64
import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock

sys.path.append(os.getcwd())

from backend.dumper.config import discover_merchant_id
from backend.kaspi_wrapper import KaspiAPI
import backend.state_helpers as state_helpers


def make_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


class TestDiscoverMerchantIdConfigModule(unittest.TestCase):
    """dumper/config.py::discover_merchant_id"""

    @patch("backend.state_helpers._load_session")
    def test_session_has_plain_digit_merchant_id(self, mock_session):
        mock_session.return_value = {"merchant_id": "20186986"}
        self.assertEqual(discover_merchant_id("tok"), "20186986")

    @patch("backend.state_helpers._load_session")
    def test_session_has_prefixed_merchant_id(self, mock_session):
        mock_session.return_value = {"merchant_id": "m_20186986"}
        self.assertEqual(discover_merchant_id("tok"), "20186986")

    @patch("backend.dumper.config.requests.get")
    @patch("backend.state_helpers._load_session")
    def test_no_session_id_resolves_via_api_with_underscore(self, mock_session, mock_get):
        mock_session.return_value = {}
        mock_get.return_value = make_response(200, {"data": [{"id": "12345_001"}]})
        self.assertEqual(discover_merchant_id("tok"), "12345")

    @patch("backend.dumper.config.requests.get")
    @patch("backend.state_helpers._load_session")
    def test_no_session_id_resolves_via_api_without_underscore(self, mock_session, mock_get):
        mock_session.return_value = {}
        mock_get.return_value = make_response(200, {"data": [{"id": "98765"}]})
        self.assertEqual(discover_merchant_id("tok"), "98765")

    @patch("backend.dumper.config.requests.get")
    @patch("backend.state_helpers._load_session")
    def test_total_failure_falls_back_to_hardcoded_default(self, mock_session, mock_get):
        mock_session.return_value = {}
        mock_get.side_effect = Exception("network down")
        self.assertEqual(discover_merchant_id("tok"), "20186986")


class TestGetMerchantIdKaspiWrapper(unittest.TestCase):
    """kaspi_wrapper.py::KaspiAPI.get_merchant_id — a SEPARATE implementation."""

    @patch("backend.kaspi_wrapper.KaspiAPI.get_point_of_services")
    def test_pointofservices_with_underscore(self, mock_pos):
        mock_pos.return_value = [{"id": "555_001"}]
        self.assertEqual(KaspiAPI.get_merchant_id("tok"), "555")

    @patch("backend.kaspi_wrapper.KaspiAPI.get_point_of_services")
    def test_pointofservices_without_underscore(self, mock_pos):
        mock_pos.return_value = [{"id": "777"}]
        self.assertEqual(KaspiAPI.get_merchant_id("tok"), "777")

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_point_of_services")
    def test_falls_back_to_orders_pickup_point_id(self, mock_pos, mock_get):
        mock_pos.side_effect = Exception("pointofservices 500")
        mock_get.return_value = make_response(200, {
            "data": [{"attributes": {"pickupPointId": "222_001"}}]
        })
        self.assertEqual(KaspiAPI.get_merchant_id("tok"), "222")

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_point_of_services")
    def test_origin_address_base64_fallback_is_effectively_dead_code(self, mock_pos, mock_get):
        """
        Обнаруженная в характеризующих тестах особенность: get_merchant_id пытается
        декодировать originAddress.id из Base64 и искать "_" в результате (:162-164),
        но _decode_base64_if_needed (:29-47) возвращает декодированное значение ТОЛЬКО
        если оно целиком цифровое — иначе возвращает исходную (всё ещё закодированную)
        строку. Для реалистичного значения вида "333_001" декодированная строка не
        цифровая (есть "_"), поэтому возвращается исходная Base64-строка, которая сама
        не содержит "_" — ветка никогда не срабатывает, и мы проваливаемся до md5-хэша.
        Флаг для Phase 4c: эта ветка не работает так, как задумано.
        """
        mock_pos.side_effect = Exception("pointofservices 500")
        encoded = base64.b64encode(b"333_001").decode("utf-8")
        mock_get.return_value = make_response(200, {
            "data": [{"attributes": {"originAddress": {"id": encoded}}}]
        })
        result = KaspiAPI.get_merchant_id("tok")
        self.assertTrue(result.startswith("m_"))
        self.assertNotEqual(result, "333")

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    @patch("backend.kaspi_wrapper.KaspiAPI.get_point_of_services")
    def test_total_failure_falls_back_to_md5_hash_not_hardcoded_default(self, mock_pos, mock_get):
        """
        КЛЮЧЕВОЕ РАСХОЖДЕНИЕ: при полном отказе оба резолвера должны сигнализировать
        "не удалось определить merchant_id", но dumper/config.py возвращает
        константу "20186986" (реальный ID одного конкретного продавца — риск
        кросс-тенантной подмены), а kaspi_wrapper.py возвращает "m_<md5>"
        (безопасный, гарантированно уникальный sentinel). Разное поведение для
        одного и того же случая отказа — то, что Phase 4c обязан унифицировать.
        """
        mock_pos.side_effect = Exception("pointofservices down")
        mock_get.side_effect = Exception("orders down")
        result = KaspiAPI.get_merchant_id("tok")
        self.assertTrue(result.startswith("m_"))
        self.assertNotEqual(result, "20186986")


class TestCookieLoaderStateHelpers(unittest.TestCase):
    """state_helpers.py::_load_mc_cookies — the plan's designated canonical version."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_reads_cookies_list_format_from_state_json(self):
        import json
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "sess123"},
                {"name": "mc-sid", "value": "sid456"},
                {"name": "other", "value": "ignored"},
            ]}, f)
        cookies = state_helpers._load_mc_cookies()
        self.assertEqual(cookies, {"mc-session": "sess123", "mc-sid": "sid456"})

    def test_reads_flat_dict_format(self):
        import json
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"mc-session": "sess1", "mc-sid": "sid1"}, f)
        cookies = state_helpers._load_mc_cookies()
        self.assertEqual(cookies, {"mc-session": "sess1", "mc-sid": "sid1"})

    def test_falls_back_to_states_session_json(self):
        import json
        os.makedirs("states", exist_ok=True)
        with open("states/session.json", "w", encoding="utf-8") as f:
            json.dump({"mc-session": "s2", "mc-sid": "sid2"}, f)
        cookies = state_helpers._load_mc_cookies()
        self.assertEqual(cookies, {"mc-session": "s2", "mc-sid": "sid2"})

    def test_no_files_present_returns_empty_dict_no_raise(self):
        cookies = state_helpers._load_mc_cookies()
        self.assertEqual(cookies, {})

    def test_incomplete_cookies_returns_empty_dict(self):
        import json
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"mc-session": "only-one"}, f)
        cookies = state_helpers._load_mc_cookies()
        self.assertEqual(cookies, {})


class TestCookieLoaderKaspiWrapper(unittest.TestCase):
    """
    kaspi_wrapper.py::KaspiAPI._load_mc_cookies — a SEPARATE, narrower
    implementation with a different failure mode.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_requires_kaspi_domain_on_cookie(self):
        import json
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "s", "domain": ".kaspi.kz"},
                {"name": "mc-sid", "value": "sid", "domain": ".kaspi.kz"},
            ]}, f)
        cookies = KaspiAPI._load_mc_cookies()
        self.assertEqual(cookies, {"mc-session": "s", "mc-sid": "sid"})

    def test_cookie_without_kaspi_domain_is_ignored(self):
        import json
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "s", "domain": "example.com"},
                {"name": "mc-sid", "value": "sid", "domain": "example.com"},
            ]}, f)
        with self.assertRaises(Exception):
            KaspiAPI._load_mc_cookies()

    def test_does_not_check_states_subdirectory_fallback(self):
        """
        РАСХОЖДЕНИЕ: в отличие от state_helpers._load_mc_cookies, эта версия
        проверяет ТОЛЬКО корневой state.json — states/session.json игнорируется.
        """
        import json
        os.makedirs("states", exist_ok=True)
        with open("states/session.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "s", "domain": ".kaspi.kz"},
                {"name": "mc-sid", "value": "sid", "domain": ".kaspi.kz"},
            ]}, f)
        with self.assertRaises(Exception):
            KaspiAPI._load_mc_cookies()

    def test_missing_cookies_raises_instead_of_returning_empty_dict(self):
        """
        КЛЮЧЕВОЕ РАСХОЖДЕНИЕ: state_helpers-версия при отсутствии кук молча
        возвращает {} (вызывающий код сам решает, что делать), а эта версия
        бросает Exception — разные контракты для одного и того же случая
        "куки не найдены", которые Phase 4c должен унифицировать.
        """
        with self.assertRaises(Exception):
            KaspiAPI._load_mc_cookies()


class TestVerifyTokenPropagatesExceptions(unittest.TestCase):
    """
    kaspi_wrapper.py::KaspiAPI.verify_token — fixed 2026-07-28 (part of
    Phase 4e). Previously ANY exception during the verification request
    (network down, DNS failure, timeout) was silently swallowed and treated
    as "token is valid", making a genuinely invalid token indistinguishable
    from a valid one during an outage. Callers (auth.py's /auth endpoint,
    stores_v1.py's connect_full_store) already had exception handling built
    around this call expecting it to raise — that handling was dead code
    until now. The fix simply removes the swallow; no caller changes needed.
    """

    def test_test_prefixed_token_short_circuits_true(self):
        self.assertTrue(KaspiAPI.verify_token("test_anything"))

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_200_response_is_valid(self, mock_get):
        mock_get.return_value = make_response(200)
        self.assertTrue(KaspiAPI.verify_token("realtoken"))

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_401_response_is_invalid(self, mock_get):
        mock_get.return_value = make_response(401)
        self.assertFalse(KaspiAPI.verify_token("realtoken"))

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_403_response_is_still_treated_as_valid(self, mock_get):
        mock_get.return_value = make_response(403)
        self.assertTrue(KaspiAPI.verify_token("realtoken"))

    @patch("backend.kaspi_wrapper.KaspiAPI._get")
    def test_network_exception_now_propagates_instead_of_swallowed(self, mock_get):
        mock_get.side_effect = Exception("connection refused")
        with self.assertRaises(Exception):
            KaspiAPI.verify_token("realtoken")


if __name__ == "__main__":
    unittest.main()
