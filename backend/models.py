from typing import Optional, List
from pydantic import BaseModel

class AuthRequest(BaseModel):
    token: str

class PackOrderRequest(BaseModel):
    order_id: str  # internal Base64 id
    order_code: Optional[str] = None
    quantity: Optional[int] = None
    new_status: str = "ACCEPTED_BY_MERCHANT"

class UpdateProductRequest(BaseModel):
    sku: str
    price: int
    stock: int
    brand: Optional[str] = None
    status: Optional[str] = None

class PhoneRequest(BaseModel):
    phone: str

class SmsRequest(BaseModel):
    sms: str

class DumpingConfigRequest(BaseModel):
    sku: str
    min_price: float
    max_price: float
    dump_step: Optional[float] = 10.0
    enabled: bool = True
    mode: Optional[str] = "TOP_1"  # OFF, TOP_1, TOP_2, MATCH_FIRST, TARGET_POSITION
    target_position: Optional[int] = 1
    max_delivery_days: Optional[int] = None
    min_seller_rating: Optional[float] = None
    same_city_only: Optional[bool] = False
    blacklisted_merchants: Optional[List[str]] = []
    city_code: Optional[str] = "750000000"

