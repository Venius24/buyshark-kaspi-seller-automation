import os
import json
from typing import Optional
from fastapi import HTTPException

SESSION_FILE = "states/session.json"
OVERRIDES_FILE = "states/products_overrides.json"
DUMPING_CONFIGS_FILE = "states/dumping_configs.json"

def _load_session() -> dict:
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_session(data: dict):
    os.makedirs("states", exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _get_token() -> str:
    """Retrieve saved token or return None."""
    session = _load_session()
    return session.get("token")

def _require_token() -> str:
    """Retrieve saved token or raise 401."""
    token = _get_token()
    if not token:
        raise HTTPException(status_code=401, detail="Токен не найден. Пожалуйста, авторизуйтесь.")
    return token

def extract_brand(name: Optional[str]) -> str:
    if not name:
        return "Barkhan"
    name_upper = name.upper()
    for b in ["BARKHAN", "RIDERACE", "CAMPGEAR", "VISION", "FIKS"]:
        if b in name_upper:
            return b.capitalize()
    return "Barkhan"

def _load_overrides() -> dict:
    if os.path.exists(OVERRIDES_FILE):
        try:
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_override(sku: str, price: int, stock: int, brand: Optional[str] = None, status: Optional[str] = None):
    overrides = _load_overrides()
    overrides[sku] = {
        "price_num": price,
        "stock": stock,
        "brand": brand,
        "status": status or ("Активен" if stock > 0 else "Архив")
    }
    os.makedirs("states", exist_ok=True)
    with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)

def _apply_overrides(products: list) -> list:
    overrides = _load_overrides()
    for p in products:
        sku = p.get("sku")
        if "price_num" not in p:
            try:
                price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                p["price_num"] = int(price_str)
            except:
                p["price_num"] = 0
        if "stock" not in p:
            p["stock"] = 10
        if "brand" not in p:
            p["brand"] = extract_brand(p.get("name"))
            
        if sku in overrides:
            ov = overrides[sku]
            if "price_num" in ov:
                p["price_num"] = ov["price_num"]
                p["price"] = f"{ov['price_num']} ₸"
            if "stock" in ov:
                p["stock"] = ov["stock"]
            if "brand" in ov:
                p["brand"] = ov["brand"]
            if "status" in ov:
                p["status"] = ov["status"]
    return products

def _load_dumping_configs() -> dict:
    if os.path.exists(DUMPING_CONFIGS_FILE):
        try:
            with open(DUMPING_CONFIGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_dumping_configs(configs: dict):
    os.makedirs("states", exist_ok=True)
    with open(DUMPING_CONFIGS_FILE, "w", encoding="utf-8") as f:
        json.dump(configs, f, ensure_ascii=False, indent=2)

MASTER_STOCK_GROUPS_FILE = "states/master_stock_groups.json"
PROCESSED_ORDERS_FILE = "states/processed_orders.json"

def _load_master_stock_groups() -> dict:
    if os.path.exists(MASTER_STOCK_GROUPS_FILE):
        try:
            with open(MASTER_STOCK_GROUPS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_master_stock_groups(groups: dict):
    os.makedirs("states", exist_ok=True)
    with open(MASTER_STOCK_GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

def _load_processed_orders() -> dict:
    if os.path.exists(PROCESSED_ORDERS_FILE):
        try:
            with open(PROCESSED_ORDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_processed_orders(orders: dict):
    os.makedirs("states", exist_ok=True)
    with open(PROCESSED_ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)

def _load_mc_cookies() -> dict:
    """
    Унифицированная загрузка кук mc-session и mc-sid из государств:
    проверяются root 'state.json', 'states/session.json', 'states/state.json'.
    """
    candidate_paths = ["state.json", "states/session.json", "states/state.json"]
    for path in candidate_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            cookies = {}
            if isinstance(data, dict):
                cookies_list = data.get("cookies", [])
                if isinstance(cookies_list, list):
                    for c in cookies_list:
                        if isinstance(c, dict) and c.get("name") in ["mc-session", "mc-sid"]:
                            cookies[c["name"]] = c["value"]
                # Также если куки за сохранены в виде прямого словаря
                if "mc-session" in data:
                    cookies["mc-session"] = data["mc-session"]
                if "mc-sid" in data:
                    cookies["mc-sid"] = data["mc-sid"]

            if "mc-session" in cookies and "mc-sid" in cookies:
                return cookies
        except Exception:
            pass
    return {}

API_KEYS_FILE = "states/api_keys.json"
WEBHOOK_CONFIG_FILE = "states/webhook_config.json"

def _load_api_keys() -> dict:
    if os.path.exists(API_KEYS_FILE):
        try:
            with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_api_keys(keys: dict):
    os.makedirs("states", exist_ok=True)
    with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, ensure_ascii=False, indent=2)

def _load_webhook_config() -> dict:
    if os.path.exists(WEBHOOK_CONFIG_FILE):
        try:
            with open(WEBHOOK_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_webhook_config(cfg: dict):
    os.makedirs("states", exist_ok=True)
    with open(WEBHOOK_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)



