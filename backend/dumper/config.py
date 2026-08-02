import base64
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

def discover_merchant_id(token: str) -> Optional[str]:
    """
    Определяет numeric Merchant ID (ID кабинета) по API-токену.

    Делегирует в KaspiAPI.get_merchant_id (kaspi_wrapper.py), у которого
    полная цепочка резолвинга: /pointofservices -> история заказов (если
    pointofservices недоступен/500) -> синтетический m_<hash> как последний
    резерв (см. get_merchant_id). Здесь синтетический хэш ЦЕЛЕНАПРАВЛЕННО
    отбрасывается в None — вызывающий код в этом модуле ожидает "не
    удалось определить" именно как None, а не как непригодный для Kaspi
    API non-digit ID (раньше здесь был отдельный урезанный вариант БЕЗ
    фоллбека через заказы вообще — из-за чего для магазинов с недоступным
    /pointofservices ID никогда не резолвился, даже если заказы были).
    """
    from backend.kaspi_wrapper import KaspiAPI
    try:
        merchant_id = KaspiAPI.get_merchant_id(token)
    except Exception as e:
        logger.debug(f"Запрос Merchant ID пропущен: {e}")
        return None
    if merchant_id and merchant_id.isdigit():
        logger.info(f"🎯 Успешно определен Merchant ID: {merchant_id}")
        return merchant_id
    return None

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

from typing import Optional, List

@dataclass
class DumpingProductConfig:
    sku: str  # Внутренний артикул (merchantSku)
    master_sku: str  # Kaspi Master SKU
    my_current_price: float
    min_price: float
    max_price: float
    step: float = 10.0
    enabled: bool = True
    mode: str = "TOP_1"  # OFF, TOP_1, TOP_2, MATCH_FIRST, TARGET_POSITION
    target_position: int = 1
    max_delivery_days: Optional[int] = None
    min_seller_rating: Optional[float] = None
    same_city_only: bool = False
    blacklisted_merchants: Optional[List[str]] = None
    city_code: str = "750000000"

