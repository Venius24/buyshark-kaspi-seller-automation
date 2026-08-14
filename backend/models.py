from typing import Optional, List, Any, Dict
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

class LoginCredentialsRequest(BaseModel):
    email: str
    password: str

class DumpingConfigRequest(BaseModel):
    sku: str
    min_price: float
    max_price: float
    dump_step: Optional[float] = 2.0
    enabled: bool = True
    mode: Optional[str] = "TOP_1"  # OFF, TOP_1, TOP_2, MATCH_FIRST, TARGET_POSITION
    target_position: Optional[int] = 1
    max_delivery_days: Optional[int] = None
    min_seller_rating: Optional[float] = None
    same_city_only: Optional[bool] = False
    blacklisted_merchants: Optional[List[str]] = []
    city_code: Optional[str] = "750000000"

class AutoAssemblyRuleRequest(BaseModel):
    delivery_type: str
    enabled: bool = False
    dry_run: bool = True
    stage_accept: bool = False
    stage_assemble: bool = False
    stage_waybill: bool = False
    schedule: Optional[Dict[str, Any]] = None  # {"days": [0..6], "start": "HH:MM", "end": "HH:MM"}
    max_order_price: Optional[float] = None
    sku_blacklist: Optional[List[str]] = []
    respect_stock_groups: bool = True
    max_per_cycle: Optional[int] = None


class MarkSpoolPrintedRequest(BaseModel):
    order_codes: Optional[List[str]] = None


class GroupDumpingConfig(BaseModel):
    enabled: bool = True
    min_price: float
    max_price: float
    dump_step: Optional[float] = 2.0
    mode: Optional[str] = "TOP_1"  # OFF, TOP_1, TOP_2, MATCH_FIRST, TARGET_POSITION
    target_position: Optional[int] = 1
    max_delivery_days: Optional[int] = None
    min_seller_rating: Optional[float] = None
    same_city_only: Optional[bool] = False
    blacklisted_merchants: Optional[List[str]] = []
    city_code: Optional[str] = "750000000"

class MasterStockGroupCreateRequest(BaseModel):
    group_name: str
    shared_stock: int
    linked_skus: List[str]
    external_code: Optional[str] = None
    is_active: Optional[bool] = True
    stop_qty: Optional[int] = 5
    # Сколько штук остатка списывать за одну проданную единицу (комплект).
    deduct_per_unit: Optional[int] = 1
    dumping_config: Optional[GroupDumpingConfig] = None
    dumping_excluded_skus: Optional[List[str]] = None

class MasterStockGroupUpdateRequest(BaseModel):
    group_name: Optional[str] = None
    shared_stock: Optional[int] = None
    linked_skus: Optional[List[str]] = None
    external_code: Optional[str] = None
    is_active: Optional[bool] = None
    stop_qty: Optional[int] = None
    deduct_per_unit: Optional[int] = None
    dumping_config: Optional[GroupDumpingConfig] = None
    dumping_excluded_skus: Optional[List[str]] = None

class UpdateStockQtyRequest(BaseModel):
    shared_stock: int

class PublicStockUpdateRequest(BaseModel):
    group_id: Optional[str] = None
    external_code: Optional[str] = None
    new_stock: int
    mode: Optional[str] = "SET"  # "SET" (exact stock) or "DELTA" (+/- relative)

class ApiKeyCreateRequest(BaseModel):
    name: str

class WebhookConfigRequest(BaseModel):
    url: str
    secret: Optional[str] = None
    enabled: Optional[bool] = True

class AutolistDraftPatchRequest(BaseModel):
    """Патч поверх draft_products.draft_payload — только изменённые поля.
    "features" сливается по ключу (attributeCode), остальные поля (sku,
    price, stock, category_code, brand_name, description...) заменяются
    целиком. См. backend/autolist/validator.py::update_draft."""
    patch: Dict[str, Any]



