import base64
import logging
import requests
from dataclasses import dataclass

logger = logging.getLogger(__name__)

def discover_merchant_id(token: str) -> str:
    """
    Автоматически определяет Merchant ID (ID кабинета) по API-токену.
    Делает GET-запрос к /pointofservices и парсит ID первой точки до знака '_'.
    """
    url = "https://kaspi.kz/shop/api/v2/pointofservices"
    headers = {
        "Content-Type": "application/vnd.api+json",
        "X-Auth-Token": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                pos_id = data[0].get("id", "")
                if "_" in pos_id:
                    merchant_id = pos_id.split("_")[0]
                    logger.info(f"🎯 Успешно определен Merchant ID: {merchant_id}")
                    return merchant_id
                elif pos_id:
                    return pos_id
        logger.warning(f"Не удалось получить Merchant ID. Статус: {resp.status_code}, Ответ: {resp.text}")
    except Exception as e:
        logger.exception(f"Ошибка при определении Merchant ID: {e}")
    return ""

def _decode_base64_if_needed(val: str) -> str:
    """Декодирует Base64 строку в числовой Kaspi Master SKU, если необходимо."""
    if not val:
        return ""
    val_str = str(val).strip()
    if val_str.isdigit():
        return val_str
    try:
        decoded = base64.b64decode(val_str).decode("utf-8")
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return val_str

@dataclass
class DumpingProductConfig:
    sku: str  # Внутренний артикул (merchantSku)
    master_sku: str  # Kaspi Master SKU
    my_current_price: float
    min_price: float
    max_price: float
    step: float = 10.0
    enabled: bool = True
    city_code: str = "750000000"
