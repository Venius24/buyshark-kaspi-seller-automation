import os
import json
from fastapi import APIRouter, HTTPException
from backend.models import DumpingConfigRequest
from backend.state_helpers import _require_token, _load_dumping_configs, _save_dumping_configs, _save_override
from backend.dumper import run_price_dumping_job

router = APIRouter(prefix="/api/dumping", tags=["dumping"])

@router.get("/configs")
def get_dumping_configs():
    """Получить все конфигурации авто-демпинга."""
    configs = _load_dumping_configs()
    return {"success": True, "configs": configs}

@router.post("/config")
def save_dumping_config(req: DumpingConfigRequest):
    """Сохранить или обновить конфигурацию демпинга для SKU."""
    configs = _load_dumping_configs()
    configs[req.sku] = {
        "min_price": req.min_price,
        "max_price": req.max_price,
        "dump_step": req.dump_step,
        "enabled": req.enabled,
        "city_code": req.city_code
    }
    _save_dumping_configs(configs)
    return {"success": True, "message": f"Конфигурация демпинга для {req.sku} сохранена."}

@router.post("/run")
async def run_price_dumping():
    """Запустить расчет авто-демпинга для включенных товаров."""
    configs = _load_dumping_configs()
    products_file = "states/products.json"
    
    if not os.path.exists(products_file):
        raise HTTPException(status_code=404, detail="Список товаров пуст. Загрузите товары.")
        
    try:
        with open(products_file, "r", encoding="utf-8") as f:
            products = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения товаров: {e}")
        
    token = _require_token()
    
    res = await run_price_dumping_job(
        token=token,
        configs=configs,
        products=products,
        save_override_func=_save_override
    )
    
    if res.get("updated_count", 0) > 0:
        try:
            with open(products_file, "w", encoding="utf-8") as f:
                json.dump(products, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка сохранения товаров: {e}")
            
    return res
