from typing import Optional, List, Any, Dict
from pydantic import BaseModel, field_validator

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

class ProductTagsRequest(BaseModel):
    """Полный набор меток товара — присланный список заменяет прежний."""
    tags: List[str] = []

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
    # Переопределение защиты от оттеснения (backend/dumper/guard.py):
    # {"mode": "INHERIT"|"CUSTOM"|"OFF", ...}. Не прислали — не трогаем.
    guard: Optional[Dict[str, Any]] = None

    @field_validator("guard")
    @classmethod
    def _check_guard(cls, v):
        from backend.dumper.guard import clean_guard_override
        return clean_guard_override(v)

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
    guard: Optional[Dict[str, Any]] = None

    @field_validator("guard")
    @classmethod
    def _check_guard(cls, v):
        from backend.dumper.guard import clean_guard_override
        return clean_guard_override(v)

class SkuRecipeComponent(BaseModel):
    group_id: str
    qty: int = 1


class SkuRecipe(BaseModel):
    """Рецепт списания артикула: qty своей группы + другие группы набора.
    См. backend/services/sku_recipes.py."""
    qty: int = 1
    extra: Optional[List[SkuRecipeComponent]] = None


class MasterStockGroupCreateRequest(BaseModel):
    group_name: str
    shared_stock: int
    linked_skus: List[str]
    external_code: Optional[str] = None
    is_active: Optional[bool] = True
    stop_qty: Optional[int] = 5
    # {артикул: рецепт}. Групповой deduct_per_unit убран (27.09): множитель
    # теперь у каждого артикула свой. Старый клиент, приславший
    # deduct_per_unit, просто игнорируется.
    sku_recipes: Optional[Dict[str, SkuRecipe]] = None
    dumping_config: Optional[GroupDumpingConfig] = None
    dumping_excluded_skus: Optional[List[str]] = None

class MasterStockGroupUpdateRequest(BaseModel):
    group_name: Optional[str] = None
    shared_stock: Optional[int] = None
    linked_skus: Optional[List[str]] = None
    external_code: Optional[str] = None
    is_active: Optional[bool] = None
    stop_qty: Optional[int] = None
    sku_recipes: Optional[Dict[str, SkuRecipe]] = None
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





class DumpingGuardSettings(BaseModel):
    """Общие настройки защиты демпинга от оттеснения (guard.GuardConfig).
    Неприсланные поля не сбрасываются в умолчания — роутер накладывает
    присланное поверх сохранённого (model_dump(exclude_unset=True))."""
    enabled: bool = False
    observe_only: bool = False
    min_reviews: Optional[int] = 50
    no_rating_action: str = "ASK"
    min_rating: Optional[float] = None
    min_merchant_age_days: Optional[int] = None
    ignore_intercity: bool = False
    max_extra_delivery_days: Optional[int] = None
    ignore_preorder: bool = False
    blacklist: List[str] = []
    whitelist: List[str] = []
    all_ignored_action: str = "HOLD"
    floor_below_next_pct: Optional[float] = None
    max_daily_drop_pct: Optional[float] = 15.0
    drop_pause_hours: int = 3
    drop_alert: bool = True
    ladder_min_moves: int = 10
    ladder_window_hours: int = 24
    ladder_min_slide_pct: Optional[float] = 10.0
    ladder_response: str = "RESET_MAX"
    ladder_cooldown_hours: int = 12
