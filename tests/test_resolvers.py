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
    """
    dumper/config.py::discover_merchant_id — Слайс 7: больше не читает общий
    states/session.json (тот кэш мог вернуть merchant_id ЧУЖОГО магазина),
    резолвит всегда через живой API-запрос по переданному токену.
    """

    @patch("backend.dumper.config.requests.get")
    def test_resolves_via_api_with_underscore(self, mock_get):
        mock_get.return_value = make_response(200, {"data": [{"id": "12345_001"}]})
        self.assertEqual(discover_merchant_id("tok"), "12345")

    @patch("backend.dumper.config.requests.get")
    def test_resolves_via_api_without_underscore(self, mock_get):
        mock_get.return_value = make_response(200, {"data": [{"id": "98765"}]})
        self.assertEqual(discover_merchant_id("tok"), "98765")

    @patch("backend.dumper.config.requests.get")
    def test_total_failure_falls_back_to_hardcoded_default(self, mock_get):
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


class TestCookieLoaderStateHelpersStoreScoped(unittest.TestCase):
    """
    state_helpers.py::_load_mc_cookies(store_id=...) — closing HANDOFF 3.1.
    store_id given must read EXCLUSIVELY from Store.encrypted_cookies in the
    DB, with NO fallback to the shared state.json/session.json files (by
    explicit user decision — a store with no/expired DB cookies must show
    "please log in again", never silently reuse another store's session).
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _write_global_state_json(self):
        import json
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "global-sess"},
                {"name": "mc-sid", "value": "global-sid"},
            ]}, f)

    @patch("backend.state_helpers._get_sync_db_session")
    def test_reads_cookies_from_store_row_when_present(self, mock_db_session):
        from backend.security.crypto import encrypt_data
        mock_session = MagicMock()
        mock_store = MagicMock()
        mock_store.encrypted_cookies = encrypt_data("mc-session=s1; mc-sid=sid1")
        mock_session.get.return_value = mock_store
        mock_db_session.return_value = mock_session

        cookies = state_helpers._load_mc_cookies(store_id="store-a")
        self.assertEqual(cookies, {"mc-session": "s1", "mc-sid": "sid1"})

    @patch("backend.state_helpers._get_sync_db_session")
    def test_no_fallback_to_global_file_when_store_has_no_cookies(self, mock_db_session):
        self._write_global_state_json()
        mock_session = MagicMock()
        mock_store = MagicMock()
        mock_store.encrypted_cookies = None
        mock_session.get.return_value = mock_store
        mock_db_session.return_value = mock_session

        cookies = state_helpers._load_mc_cookies(store_id="store-a")
        self.assertEqual(cookies, {}, "must not fall back to state.json even though it has valid cookies")

    @patch("backend.state_helpers._get_sync_db_session")
    def test_store_not_found_returns_empty_dict(self, mock_db_session):
        self._write_global_state_json()
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_db_session.return_value = mock_session

        cookies = state_helpers._load_mc_cookies(store_id="ghost-store")
        self.assertEqual(cookies, {})

    @patch("backend.state_helpers._get_sync_db_session")
    def test_unscoped_call_still_reads_global_file_unchanged(self, mock_db_session):
        """store_id=None must be byte-for-byte the pre-3.1 behavior — no DB touched at all."""
        self._write_global_state_json()
        cookies = state_helpers._load_mc_cookies(store_id=None)
        self.assertEqual(cookies, {"mc-session": "global-sess", "mc-sid": "global-sid"})
        mock_db_session.assert_not_called()


class TestMcCredentialsAndCookiesStoreScoped(unittest.TestCase):
    """
    state_helpers.py::_save_mc_credentials/_load_mc_credentials/
    _save_mc_cookies_for_store — per-store DB persistence (HANDOFF 3.1).
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch("backend.state_helpers._get_sync_db_session")
    def test_save_and_load_credentials_round_trip_per_store(self, mock_db_session):
        mock_session = MagicMock()
        mock_store = MagicMock()
        mock_store.encrypted_mc_email = None
        mock_store.encrypted_mc_password = None
        mock_session.get.return_value = mock_store
        mock_db_session.return_value = mock_session

        state_helpers._save_mc_credentials("a@b.kz", "hunter2", store_id="store-a")
        # Capture what got assigned onto the mock store object.
        self.assertIsNotNone(mock_store.encrypted_mc_email)
        self.assertIsNotNone(mock_store.encrypted_mc_password)

        creds = state_helpers._load_mc_credentials(store_id="store-a")
        self.assertEqual(creds, ("a@b.kz", "hunter2"))

    @patch("backend.state_helpers._get_sync_db_session")
    def test_load_credentials_no_store_row_returns_none_no_fallback(self, mock_db_session):
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_db_session.return_value = mock_session

        creds = state_helpers._load_mc_credentials(store_id="ghost")
        self.assertIsNone(creds)

    @patch("backend.state_helpers._get_sync_db_session")
    def test_save_cookies_for_store_requires_both_cookies_present(self, mock_db_session):
        mock_session = MagicMock()
        mock_db_session.return_value = mock_session
        ok = state_helpers._save_mc_cookies_for_store("store-a", {"mc-session": "only-one"})
        self.assertFalse(ok)
        mock_session.get.assert_not_called()

    @patch("backend.state_helpers._get_sync_db_session")
    def test_save_cookies_for_store_persists_and_activates(self, mock_db_session):
        mock_session = MagicMock()
        mock_store = MagicMock()
        mock_session.get.return_value = mock_store
        mock_db_session.return_value = mock_session

        ok = state_helpers._save_mc_cookies_for_store("store-a", {"mc-session": "s", "mc-sid": "sid"})
        self.assertTrue(ok)
        self.assertEqual(mock_store.status, "ACTIVE")
        mock_session.commit.assert_called_once()


class TestRefreshMcSessionStoreScoped(unittest.TestCase):
    """state_helpers.py::_refresh_mc_session(store_id=...) — no guessing, no fallback creds."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @patch("backend.state_helpers._load_mc_credentials")
    def test_no_saved_creds_for_store_returns_false_immediately(self, mock_load_creds):
        mock_load_creds.return_value = None
        result = state_helpers._refresh_mc_session(store_id="store-a")
        self.assertFalse(result)
        mock_load_creds.assert_called_once_with(store_id="store-a")

    @patch("backend.services.playwright_worker.worker")
    @patch("backend.state_helpers._save_mc_cookies_for_store")
    @patch("backend.state_helpers._load_mc_credentials")
    def test_successful_relogin_syncs_fresh_cookies_into_store_row(self, mock_load_creds, mock_save_for_store, mock_worker):
        mock_load_creds.return_value = ("a@b.kz", "hunter2")
        # Де-глобализация Playwright: LOGIN отдаёт куки напрямую (3-tuple),
        # не через общий state.json — см. playwright_worker.py.
        mock_worker.send_cmd.return_value = (True, "Success", {"mc-session": "fresh", "mc-sid": "fresh-sid"})

        result = state_helpers._refresh_mc_session(store_id="store-a")

        self.assertTrue(result)
        mock_save_for_store.assert_called_once_with("store-a", {"mc-session": "fresh", "mc-sid": "fresh-sid"})


class TestCookieLoaderKaspiWrapper(unittest.TestCase):
    """
    kaspi_wrapper.py::KaspiAPI._load_mc_cookies — UNIFIED (2026-07-29, closing
    HANDOFF 3.1) to delegate to state_helpers._load_mc_cookies instead of
    duplicating its own narrower state.json parsing. The divergences this
    class used to document (domain-check requirement, no states/ fallback,
    raise-vs-{}) are gone: the only remaining difference is that the
    KaspiAPI wrapper still raises when cookies are missing (callers like
    assemble_cargo rely on that), while state_helpers._load_mc_cookies
    itself returns {} and lets the caller decide.
    """

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_domain_on_cookie_is_no_longer_required(self):
        """Unified implementation doesn't look at the cookie's domain field at all."""
        import json
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "s", "domain": "example.com"},
                {"name": "mc-sid", "value": "sid", "domain": "example.com"},
            ]}, f)
        cookies = KaspiAPI._load_mc_cookies()
        self.assertEqual(cookies, {"mc-session": "s", "mc-sid": "sid"})

    def test_now_checks_states_subdirectory_fallback(self):
        """Unified implementation shares state_helpers' states/session.json fallback."""
        import json
        os.makedirs("states", exist_ok=True)
        with open("states/session.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "s"},
                {"name": "mc-sid", "value": "sid"},
            ]}, f)
        cookies = KaspiAPI._load_mc_cookies()
        self.assertEqual(cookies, {"mc-session": "s", "mc-sid": "sid"})

    def test_missing_cookies_still_raises(self):
        """
        KaspiAPI._load_mc_cookies keeps its raise-on-missing contract (callers
        like assemble_cargo depend on it), even though the shared
        state_helpers._load_mc_cookies it delegates to returns {} instead.
        """
        with self.assertRaises(Exception):
            KaspiAPI._load_mc_cookies()

    @patch("backend.state_helpers._get_sync_db_session")
    def test_store_scoped_read_with_no_db_cookies_raises_not_falls_back(self, mock_db_session):
        """
        store_id given: even if a perfectly valid global state.json sits right
        next to it, a store with no encrypted_cookies in the DB must NOT fall
        back to it — per 3.1's closure, per-store reads never touch the
        legacy shared file. KaspiAPI's wrapper still raises on "no cookies"
        (its existing contract), same as the unscoped case above.
        """
        import json
        with open("state.json", "w", encoding="utf-8") as f:
            json.dump({"cookies": [
                {"name": "mc-session", "value": "s"},
                {"name": "mc-sid", "value": "sid"},
            ]}, f)

        mock_session = MagicMock()
        mock_store = MagicMock()
        mock_store.encrypted_cookies = None
        mock_session.get.return_value = mock_store
        mock_db_session.return_value = mock_session

        with self.assertRaises(Exception):
            KaspiAPI._load_mc_cookies(store_id="store-a")


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
