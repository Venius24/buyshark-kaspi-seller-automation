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
        # debug, а не info: get_merchant_id кэширует результат в памяти
        # процесса, но зовут его горячие циклы — на проде это давало 68 строк
        # «успешно определён» за 10 минут при ВСЕГО ТРЁХ реальных походах в
        # Kaspi. Само событие резолва логируется там, где оно происходит.
        logger.debug(f"Merchant ID: {merchant_id}")
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



def build_sku_alias_map(products) -> dict:
    """
    Псевдоним оффера -> его канонический ключ (merchantSku).

    Даёт возможность сравнивать SKU "по физическому товару", а не по строке:
    "102050852" (код мастер-карточки Kaspi) и "247353364" (наш merchantSku) —
    один и тот же оффер, и настройка, заданная на любом из них, должна
    действовать на оба.
    """
    alias_map: dict = {}
    for p in products or []:
        merchant_sku = p.get("merchantSku")
        canonical = str(merchant_sku or p.get("sku") or p.get("kaspiSku") or "")
        if not canonical:
            continue
        for alias in (p.get("sku"), p.get("merchantSku"), p.get("kaspiSku")):
            if alias:
                alias_map[str(alias)] = canonical
    return alias_map


def build_effective_dumping_configs(products, configs: dict, master_groups: dict):
    """
    Реально действующие настройки демпинга: индивидуальные конфиги SKU,
    поверх которых разворачиваются настройки активных Мастер-Групп.

    Вынесено из run_price_dumping_job, чтобы UI и движок отвечали на вопрос
    "демпингуется ли этот товар" ОДНИМ И ТЕМ ЖЕ кодом. Раньше вкладка
    демпинга смотрела только на индивидуальные конфиги и показывала «демпинг
    выключен» у товаров, которые движок в это же время исправно демпинговал
    по групповой настройке.

    Возвращает (effective_configs, source_by_sku), где source_by_sku:
    sku -> "individual" | "group".
    """
    effective_configs = dict(configs or {})
    source_by_sku = {sku: "individual" for sku in effective_configs}

    canonical = build_sku_alias_map(products)

    for _gid, group in (master_groups or {}).items():
        if not group.get("is_active", True):
            continue
        g_dump_cfg = group.get("dumping_config")
        if not (g_dump_cfg and g_dump_cfg.get("enabled")):
            continue
        # Один физический оффер известен под двумя идентификаторами (наш
        # merchantSku и код мастер-карточки Kaspi), и в linked_skus регулярно
        # лежат ОБА — поэтому исключение, заданное одним идентификатором,
        # разворачиваем на все псевдонимы.
        excluded_skus = {
            canonical.get(str(s), str(s)) for s in (group.get("dumping_excluded_skus") or [])
        }
        # Настройки защиты от оттеснения группы — средний слой наследования
        # «магазин → группа → товар» (backend/dumper/guard.py). Действуют и
        # на товары группы со своим конфигом демпинга: свой конфиг меняет
        # цены/режим, а не то, от кого защищаться, — защиту товар
        # переопределяет отдельно своим полем guard.
        group_guard = g_dump_cfg.get("guard")
        for sku in group.get("linked_skus", []):
            if canonical.get(str(sku), str(sku)) in excluded_skus:
                # Товар явно исключён из группового демпинга — групповой
                # конфиг его не касается, но собственный individual конфиг
                # (если задан) продолжает работать как обычно.
                continue
            indiv_cfg = effective_configs.get(sku)
            # Если индивидуального конфига нет или он выключен — применяем демпинг группы
            if not indiv_cfg or not indiv_cfg.get("enabled"):
                merged = dict(g_dump_cfg)
                merged.pop("guard", None)
                merged["sku"] = sku
                merged["group_guard"] = group_guard
                effective_configs[sku] = merged
                source_by_sku[sku] = "group"
            elif group_guard and "group_guard" not in indiv_cfg:
                effective_configs[sku] = {**indiv_cfg, "group_guard": group_guard}

    return effective_configs, source_by_sku
