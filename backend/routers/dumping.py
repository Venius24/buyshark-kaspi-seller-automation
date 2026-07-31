import time
import random
import asyncio
import logging
import functools
from fastapi import APIRouter, Depends, HTTPException
from backend.models import DumpingConfigRequest
from backend.state_helpers import _load_dumping_configs, _save_dumping_configs, _save_override, _apply_overrides, _load_products, _save_products
from backend.dumper import run_price_dumping_job
from backend.security.store_context import StoreContext, get_store_context, list_active_stores

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dumping", tags=["dumping"])

class _SchedulerState:
    def __init__(self):
        self.last_run_timestamp: float = 0.0
        self.next_run_timestamp: float = time.time() + 300.0
        self.is_running: bool = False

# Слайс 5: раньше был единый class-level DumpingSchedulerState — "последний
# запуск" одного магазина перезаписывал показания статуса для всех остальных.
# Ключ None — состояние общего фонового цикла (next_run_timestamp/is_running
# самого шедулера, он один физический asyncio-таск на всё приложение);
# per-store ключи — когда именно ЭТОТ магазин последний раз реально
# демпинговался (из шедулера или ручной кнопки "Запустить сейчас").
_scheduler_states: dict = {}

def _get_scheduler_state(store_id=None) -> _SchedulerState:
    if store_id not in _scheduler_states:
        _scheduler_states[store_id] = _SchedulerState()
    return _scheduler_states[store_id]

async def _run_dumping_cycle(token: str, store_id) -> dict:
    """
    Один проход демпинга для одного магазина. Каталог товаров теперь per-store
    (Слайс 1, см. state_helpers._load_products) — store_id=None (легаси
    dev-бутстрап без Store в БД) читает старый общий файл, как и раньше.
    """
    products = _load_products(store_id)
    if not products:
        return {"updated_count": 0}
    products = _apply_overrides(products, store_id=store_id)

    configs = _load_dumping_configs(store_id=store_id)
    save_override_func = functools.partial(_save_override, store_id=store_id)

    res = await run_price_dumping_job(
        token=token,
        configs=configs,
        products=products,
        save_override_func=save_override_func,
        store_id=store_id
    )

    if res.get("updated_count", 0) > 0:
        _save_products(products, store_id)

    return res

async def run_scheduled_dumping_cycle() -> dict:
    """
    Один запланированный проход демпинга по всем активным сторам.

    Слайс 7: легаси-фоллбек на общую сессию (когда в БД ещё нет ни одного
    Store) убран — нет активных сторов, нечего демпинговать, цикл просто
    ничего не делает вместо угадывания токена из общего states/session.json.
    """
    total_updated = 0
    stores = await list_active_stores()

    logger.info(f"🤖 [Scheduler] Running automated 5-minute price dumper cycle for {len(stores)} store(s)...")
    for store in stores:
        try:
            res = await _run_dumping_cycle(store.token, store.store_id)
            total_updated += res.get("updated_count", 0)
            _get_scheduler_state(store.store_id).last_run_timestamp = time.time()
        except Exception as e:
            logger.error(f"Scheduled dumper run failed for store {store.store_id}: {e}")

    _get_scheduler_state(None).last_run_timestamp = time.time()
    logger.info(f"🤖 [Scheduler] Automated cycle finished. Applied changes: {total_updated}.")
    return {"updated_count": total_updated, "stores_processed": len(stores)}

def _next_interval_seconds() -> float:
    # Джиттер ±45с вокруг 5 минут: ровные тики раз в 300с — заметный для
    # антибот-систем паттерн бота, случайный разброс делает трафик менее
    # предсказуемым (см. разбор IP-бана Kaspi, 2026-07-30).
    return 300.0 + random.uniform(-45.0, 45.0)

async def background_dumping_loop():
    logger.info("Background price dumping loop initialized (~5-minute interval, jittered).")
    global_state = _get_scheduler_state(None)
    # При запуске ставим первую авто-проверку через ~5 минут
    global_state.next_run_timestamp = time.time() + _next_interval_seconds()

    while True:
        try:
            now = time.time()
            if now >= global_state.next_run_timestamp:
                # Планируем следующий запуск
                global_state.next_run_timestamp = now + _next_interval_seconds()
                global_state.is_running = True
                try:
                    await run_scheduled_dumping_cycle()
                except Exception as e:
                    logger.error(f"Error during scheduled dumper run: {e}")
                finally:
                    global_state.is_running = False

            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in background scheduler loop: {e}")
            await asyncio.sleep(5)

@router.get("/configs")
def get_dumping_configs(ctx: StoreContext = Depends(get_store_context)):
    """Получить все конфигурации авто-демпинга."""
    configs = _load_dumping_configs(store_id=ctx.store_id)
    return {"success": True, "configs": configs}

@router.post("/config")
def save_dumping_config(req: DumpingConfigRequest, ctx: StoreContext = Depends(get_store_context)):
    """Сохранить или обновить конфигурацию демпинга для SKU."""
    configs = _load_dumping_configs(store_id=ctx.store_id)
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
    _save_dumping_configs(configs, store_id=ctx.store_id)
    return {"success": True, "message": f"Конфигурация демпинга для {req.sku} сохранена."}

@router.get("/status")
def get_dumping_status(ctx: StoreContext = Depends(get_store_context)):
    """
    Получить статус и таймер фонового авто-демпинга.

    Слайс 5: "когда этот магазин последний раз реально демпинговался" —
    per-store (раньше ручной запуск ОДНОГО магазина показывал "выполнено
    только что" всем остальным). Таймер/флаг общего фонового цикла
    (next_run_seconds/is_running) — общий, т.к. это один физический
    asyncio-таск на всё приложение, а не по одному на магазин.
    """
    now = time.time()
    global_state = _get_scheduler_state(None)
    store_state = _get_scheduler_state(ctx.store_id)
    next_run_seconds = max(0, int(global_state.next_run_timestamp - now))
    return {
        "success": True,
        "last_run_time": store_state.last_run_timestamp,
        "next_run_seconds": next_run_seconds,
        "is_running": global_state.is_running or store_state.is_running
    }

@router.post("/run")
async def run_price_dumping(ctx: StoreContext = Depends(get_store_context)):
    """Запустить расчет авто-демпинга вручную для стора текущего пользователя."""
    if not _load_products(ctx.store_id):
        raise HTTPException(status_code=404, detail="Список товаров пуст. Загрузите товары.")

    store_state = _get_scheduler_state(ctx.store_id)
    store_state.is_running = True
    try:
        try:
            res = await _run_dumping_cycle(ctx.token, ctx.store_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка выполнения демпинга: {e}")

        # Сбрасываем таймер следующего запуска общего цикла, так как только что выполнили
        _get_scheduler_state(None).next_run_timestamp = time.time() + 300.0
        store_state.last_run_timestamp = time.time()
        return res
    finally:
        store_state.is_running = False
