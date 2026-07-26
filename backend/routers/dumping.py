import os
import json
import time
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from backend.models import DumpingConfigRequest
from backend.state_helpers import _require_token, _get_token, _load_dumping_configs, _save_dumping_configs, _save_override, _apply_overrides
from backend.dumper import run_price_dumping_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dumping", tags=["dumping"])

class DumpingSchedulerState:
    last_run_timestamp: float = 0.0
    next_run_timestamp: float = time.time() + 300.0
    is_running: bool = False

async def background_dumping_loop():
    logger.info("Background price dumping loop initialized (5-minute interval).")
    # При запуске ставим первую авто-проверку через 5 минут
    DumpingSchedulerState.next_run_timestamp = time.time() + 300.0
    
    while True:
        try:
            now = time.time()
            if now >= DumpingSchedulerState.next_run_timestamp:
                # Планируем следующий запуск
                DumpingSchedulerState.next_run_timestamp = now + 300.0
                
                token = _get_token()
                if not token:
                    continue
                    
                products_file = "states/products.json"
                if not os.path.exists(products_file):
                    continue
                    
                DumpingSchedulerState.is_running = True
                try:
                    with open(products_file, "r", encoding="utf-8") as f:
                        products = json.load(f)
                    products = _apply_overrides(products)
                    
                    configs = _load_dumping_configs()
                    logger.info("🤖 [Scheduler] Running automated 5-minute price dumper cycle...")
                    res = await run_price_dumping_job(
                        token=token,
                        configs=configs,
                        products=products,
                        save_override_func=_save_override
                    )
                    
                    if res.get("updated_count", 0) > 0:
                        with open(products_file, "w", encoding="utf-8") as f:
                            json.dump(products, f, ensure_ascii=False, indent=2)
                    
                    DumpingSchedulerState.last_run_timestamp = time.time()
                    logger.info(f"🤖 [Scheduler] Automated cycle finished. Applied changes: {res.get('updated_count', 0)}.")
                except Exception as e:
                    logger.error(f"Error during scheduled dumper run: {e}")
                finally:
                    DumpingSchedulerState.is_running = False
            
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in background scheduler loop: {e}")
            await asyncio.sleep(5)

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
        "mode": req.mode or "TOP_1",
        "target_position": req.target_position or 1,
        "max_delivery_days": req.max_delivery_days,
        "min_seller_rating": req.min_seller_rating,
        "same_city_only": bool(req.same_city_only),
        "blacklisted_merchants": req.blacklisted_merchants or [],
        "city_code": req.city_code or "750000000"
    }
    _save_dumping_configs(configs)
    return {"success": True, "message": f"Конфигурация демпинга для {req.sku} сохранена."}

@router.get("/status")
def get_dumping_status():
    """Получить статус и таймер фонового авто-демпинга."""
    now = time.time()
    next_run_seconds = max(0, int(DumpingSchedulerState.next_run_timestamp - now))
    return {
        "success": True,
        "last_run_time": DumpingSchedulerState.last_run_timestamp,
        "next_run_seconds": next_run_seconds,
        "is_running": DumpingSchedulerState.is_running
    }

@router.post("/run")
async def run_price_dumping():
    """Запустить расчет авто-демпинга вручную."""
    configs = _load_dumping_configs()
    products_file = "states/products.json"
    
    if not os.path.exists(products_file):
        raise HTTPException(status_code=404, detail="Список товаров пуст. Загрузите товары.")
        
    try:
        with open(products_file, "r", encoding="utf-8") as f:
            products = json.load(f)
        products = _apply_overrides(products)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения товаров: {e}")
        
    token = _require_token()
    
    DumpingSchedulerState.is_running = True
    try:
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
        
        # Сбрасываем таймер следующего запуска, так как только что выполнили
        DumpingSchedulerState.next_run_timestamp = time.time() + 300.0
        DumpingSchedulerState.last_run_timestamp = time.time()
        return res
    finally:
        DumpingSchedulerState.is_running = False
