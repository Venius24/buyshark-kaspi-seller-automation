import time
import random
import asyncio
import logging
import functools
from fastapi import APIRouter, Depends, HTTPException
from backend.models import DumpingConfigRequest, DumpingGuardSettings
from backend.state_helpers import _load_dumping_configs, _save_dumping_configs, _save_override, _apply_overrides, _load_products, _save_products
from backend.dumper import run_price_dumping_job
from backend.security.store_context import StoreContext, get_store_context, get_store_context_billed, list_active_stores
from backend.security.store_roles import require_store_manager_billed, store_section_guard
from backend.routers.auth_v1 import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dumping", tags=["dumping"], dependencies=[Depends(store_section_guard("dumping"))])

class _SchedulerState:
    def __init__(self):
        self.last_run_timestamp: float = 0.0
        self.next_run_timestamp: float = time.time() + 900.0
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

    logger.info(f"🤖 [Scheduler] Running automated 15-minute price dumper cycle for {len(stores)} store(s)...")
    for store in stores:
        try:
            from backend.billing import enforcement as billing_enforcement
            blocked, _reason = await asyncio.to_thread(billing_enforcement.is_service_blocked, store.store_id)
            if blocked:
                continue
            from backend import feature_flags
            if not await asyncio.to_thread(feature_flags.is_dumping_enabled, store.store_id):
                continue
            res = await _run_dumping_cycle(store.token, store.store_id)
            updated = res.get("updated_count", 0)
            total_updated += updated
            _get_scheduler_state(store.store_id).last_run_timestamp = time.time()
            if updated > 0:
                try:
                    from backend.chatbot import notify_dispatch
                    await notify_dispatch.dispatch(
                        store.store_id, "money",
                        f"📉 Демпинг поставил в очередь новую цену у {updated} товар(ов)",
                        card="dumping",
                    )
                except Exception as notif_ex:
                    logger.error(f"[Dumping] Не удалось отправить уведомление о демпинге: {notif_ex}")
        except Exception as e:
            logger.error(f"Scheduled dumper run failed for store {store.store_id}: {e}")

    _get_scheduler_state(None).last_run_timestamp = time.time()
    logger.info(f"🤖 [Scheduler] Automated cycle finished. Applied changes: {total_updated}.")
    return {"updated_count": total_updated, "stores_processed": len(stores)}

def _next_interval_seconds() -> float:
    # Джиттер ±90с вокруг 15 минут: ровные тики — заметный для антибот-систем
    # паттерн бота, случайный разброс делает трафик менее предсказуемым (см.
    # разбор IP-бана Kaspi, 2026-07-30). Интервал поднят с 5 до 15 минут
    # 2026-08-12 — при 26 активных SKU в демпинге 5-минутный тик мог утроить
    # свой же расход квоты (до 6 циклов на 30-минутное окно Kaspi вместо 2),
    # см. HANDOFF 3.42.
    return 900.0 + random.uniform(-90.0, 90.0)

async def background_dumping_loop():
    logger.info("Background price dumping loop initialized (~15-minute interval, jittered).")
    global_state = _get_scheduler_state(None)
    # При запуске ставим первую авто-проверку через ~15 минут
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
def save_dumping_config(req: DumpingConfigRequest, ctx: StoreContext = Depends(require_store_manager_billed)):
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
    # Защиту пишем, только если форма её прислала (None = «наследовать»).
    if "guard" in req.model_fields_set:
        configs[req.sku]["guard"] = req.guard
    # Пишем только изменённый SKU: _save_dumping_configs делает SELECT+upsert
    # на каждый ключ переданного словаря, и весь конфиг магазина (сотни SKU)
    # ре-трогался бы ради правки одного — единственная причина, по которой
    # сохранение одного тумблера ощущалось медленным.
    _save_dumping_configs({req.sku: configs[req.sku]}, store_id=ctx.store_id)
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

    # Квота Kaspi на обновление офферов (250 / 30 мин). Берём ТОЛЬКО из кэша
    # (peek): этот эндпоинт фронтенд поллит раз в 10 секунд, поход в Kaspi
    # отсюда превратился бы в 360 запросов/час на каждую открытую вкладку.
    # Кэш наполняет флашер offer_sync раз в 45 секунд.
    from backend.services.kaspi_quota import P0_RESERVE, quota
    from backend.services.offer_sync import queue_depth

    quota_block = None
    snapshot = quota.peek(ctx.store_id)
    if snapshot:
        quota_block = {
            "used": snapshot.used,
            "limit": snapshot.limit,
            "remaining": snapshot.remaining,
            "reset_in_seconds": int(snapshot.seconds_until_reset()),
            "reserved_for_withdraw": P0_RESERVE,
            # used — счётчик Kaspi по ВСЕМУ мерчанту: в него попадают записи
            # любых других интеграций, работающих с тем же кабинетом. Наш
            # собственный расход считаем сами (см. quota.own_used), иначе
            # пользователь видит чужие записи как свои.
            "used_by_us": quota.own_used(ctx.store_id),
        }

    return {
        "success": True,
        "last_run_time": store_state.last_run_timestamp,
        "next_run_seconds": next_run_seconds,
        "is_running": global_state.is_running or store_state.is_running,
        "quota": quota_block,
        "queue_pending": queue_depth(ctx.store_id),
    }

@router.post("/run")
async def run_price_dumping(ctx: StoreContext = Depends(require_store_manager_billed)):
    """Запустить расчет авто-демпинга вручную для стора текущего пользователя."""
    from backend import feature_flags
    if not feature_flags.is_dumping_enabled(ctx.store_id):
        raise HTTPException(status_code=409, detail="Демпинг отключён в Глобальном управлении.")
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
        _get_scheduler_state(None).next_run_timestamp = time.time() + _next_interval_seconds()
        store_state.last_run_timestamp = time.time()
        return res
    finally:
        store_state.is_running = False


# ---------------------------------------------------------------------------
# Защита от оттеснения (backend/dumper/guard.py)
# ---------------------------------------------------------------------------

@router.get("/guard")
def get_dumping_guard(ctx: StoreContext = Depends(get_store_context)):
    """Общие настройки защиты магазина + подписи причин для интерфейса."""
    from backend.dumper import guard
    from backend.state_helpers import _load_store_guard
    settings = guard.GuardConfig.from_dict(_load_store_guard(ctx.store_id)).to_dict()
    return {"success": True, "settings": settings, "reason_labels": guard.REASON_LABELS}


@router.put("/guard")
def save_dumping_guard(req: DumpingGuardSettings, ctx: StoreContext = Depends(require_store_manager_billed)):
    from backend.dumper import guard
    from backend.state_helpers import _save_store_guard, _load_store_guard
    current = guard.GuardConfig.from_dict(_load_store_guard(ctx.store_id)).to_dict()
    try:
        clean = guard.clean_guard_settings({**current, **req.model_dump(exclude_unset=True)})
    except guard.GuardValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _save_store_guard(ctx.store_id, clean)
    return {"success": True, "settings": clean}


@router.get("/attacks")
def get_dumping_attacks(ctx: StoreContext = Depends(get_store_context)):
    """Отчёт «Под атакой»: товары с признаками оттеснения, соперники,
    которые встречаются на нескольких товарах, и сколько ₸ с единицы съела
    война за 30 дней."""
    from backend.services import dumping_attacks
    return {"success": True, **dumping_attacks.build_report(ctx.store_id)}


@router.get("/guard/decisions")
def get_guard_decisions(ctx: StoreContext = Depends(get_store_context)):
    """Продавцы без рейтинга рядом с нами: ждут решения менеджера и уже решённые."""
    from backend.state_helpers import _list_guard_decisions
    return {
        "success": True,
        "pending": _list_guard_decisions(ctx.store_id, pending_only=True),
        "decided": _list_guard_decisions(ctx.store_id, pending_only=False),
    }


@router.post("/guard/decisions/{decision_id}")
def set_guard_decision(
    decision_id: int,
    body: dict,
    ctx: StoreContext = Depends(require_store_manager_billed),
    user=Depends(get_current_user),
):
    """decision: IGNORE (не учитывать на этом товаре), CONSIDER (учитывать),
    IGNORE_ALL (в чёрный список магазина + IGNORE здесь), ASK (отменить решение)."""
    from backend.state_helpers import _set_guard_decision, _get_guard_decision, _guard_blacklist_merchant
    decision = str((body or {}).get("decision") or "").upper()
    if decision not in ("IGNORE", "CONSIDER", "IGNORE_ALL", "ASK"):
        raise HTTPException(status_code=422, detail="decision: IGNORE, CONSIDER, IGNORE_ALL или ASK")
    row = _get_guard_decision(decision_id)
    if not row or row["store_id"] != ctx.store_id:
        raise HTTPException(status_code=404, detail="Решение не найдено")
    if decision == "IGNORE_ALL":
        _guard_blacklist_merchant(ctx.store_id, row["merchant_id"])
        decision = "IGNORE"
    who = getattr(user, "email", None) or "cabinet"
    saved = _set_guard_decision(decision_id, decision, who, store_id=ctx.store_id)
    saved.pop("store_id", None)
    return {"success": True, "decision": saved}


@router.get("/attacks/{sku}/moves")
def get_dumping_attack_moves(sku: str, days: int = 14, ctx: StoreContext = Depends(get_store_context)):
    """История наших ходов по одному товару — для графика лесенки."""
    from backend.state_helpers import _load_price_war_moves
    days = max(1, min(int(days), 45))
    moves = _load_price_war_moves(ctx.store_id, hours=days * 24, sku=sku).get(sku, [])
    for m in moves:
        m["created_at"] = m["created_at"].isoformat()
    return {"success": True, "sku": sku, "moves": moves}
