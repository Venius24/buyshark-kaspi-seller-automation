import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.append(os.getcwd())

from fastapi.testclient import TestClient

from backend.main import app
from backend.security.auth import create_access_token
from backend.db.models import User, Store


def make_scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    scalars_result = MagicMock()
    scalars_result.first.return_value = value
    return_scalars = scalars_result
    result.scalars.return_value = return_scalars
    return result


class TestProductsMatchConfigByMerchantSku(unittest.TestCase):
    """Регрессия 2026-08-13: /api/products матчил конфиг демпинга ТОЛЬКО по
    Kaspi sku, а конфиги пишутся по merchantSku (импорт из PriceFeed, панель
    демпинга). На проде 374 из 377 конфигов были невидимы: товары отдавались
    как «демпинг выключен», фильтр dumping_only отдавал пустоту, и успешный
    импорт выглядел несостоявшимся. Движок при этом матчит merchantSku →
    sku (backend/dumper/radar.py:216) — ручка обязана делать так же."""

    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmp_dir = tempfile.mkdtemp()
        os.chdir(self._tmp_dir)
        os.makedirs("states", exist_ok=True)

        from backend import state_helpers
        state_helpers._save_products([{
            "sku": "179568848", "name": "Товар с артикулом", "price": "1000 ₸",
            "status": "Активен", "merchantSku": "T073-0004-1", "kaspiSku": "179568848",
        }], store_id="store-1")
        state_helpers._save_dumping_configs({
            "T073-0004-1": {
                "min_price": 249, "max_price": 390, "dump_step": 2,
                "enabled": True, "mode": "TOP_2", "target_position": 2,
                "same_city_only": False, "blacklisted_merchants": [],
                "city_code": "750000000",
            }
        }, store_id="store-1")

        self.client = TestClient(app)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _get_products(self, mock_decrypt, query=""):
        access_tok = create_access_token({"sub": "user-1"})
        user = User(id="user-1", email="a@b.com", password_hash="x", is_active=True)
        store = Store(id="store-1", merchant_id="m_new", name="Shop", encrypted_api_key="cipher")
        mock_decrypt.return_value = "tok"

        fake_db = AsyncMock()
        fake_db.execute.side_effect = [make_scalar_result(user), make_scalar_result(store)]

        async def fake_get_db_gen():
            yield fake_db

        from backend.db.database import get_db as real_get_db
        app.dependency_overrides[real_get_db] = fake_get_db_gen
        try:
            resp = self.client.get(
                f"/api/products{query}", headers={"Authorization": f"Bearer {access_tok}"}
            )
            self.assertEqual(resp.status_code, 200)
            return resp.json()
        finally:
            app.dependency_overrides.pop(real_get_db, None)

    @patch("backend.security.store_context.decrypt_data")
    def test_config_attached_when_keyed_by_merchant_sku(self, mock_decrypt):
        product = self._get_products(mock_decrypt)["products"][0]
        self.assertIsNotNone(product["dumping_config"])
        self.assertEqual(product["dumping_config"]["min_price"], 249)
        self.assertNotEqual(product["dumping_status"], "OFF")

    @patch("backend.security.store_context.decrypt_data")
    def test_dumping_only_filter_sees_merchant_sku_configs(self, mock_decrypt):
        data = self._get_products(mock_decrypt, "?dumping_only=true")
        self.assertEqual([p["sku"] for p in data["products"]], ["179568848"])


if __name__ == "__main__":
    unittest.main()
