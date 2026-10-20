import asyncio
import logging
from typing import List
from backend.dumper.config import (
    discover_merchant_id,
    _decode_base64_if_needed,
    DumpingProductConfig,
    build_sku_alias_map,
    build_effective_dumping_configs,
)
from backend.dumper.radar import RadarWorker, fetch_competitor_offers, _fetch_offers_sync
from backend.dumper.brain import BrainWorker, DumpingEngine
from backend.dumper.executor import RateLimitExecutorWorker
from backend.dumper.xml_feed import generate_xml_feed

logger = logging.getLogger(__name__)


def _load_guard_data(store_id) -> dict:
    """Всё, что нужно защите от оттеснения на один цикл: общие настройки
    магазина, история наших ходов (72 часа — с запасом на окно лесенки,
    перемирие и сутки до сброса лесенки) и решения менеджера по продавцам
    без рейтинга. От чего меряется падение цены, считает guard.drop_state.
    Ошибка чтения не должна останавливать демпинг — защита тогда просто
    не сработает в этом цикле."""
    import datetime as _dt
    data = {"store_defaults": {}, "moves_by_sku": {}, "decisions_by_sku": {}, "now": _dt.datetime.utcnow()}
    if store_id is None:
        return data
    try:
        from backend.state_helpers import _load_store_guard, _load_price_war_moves, _load_guard_decisions
        data["store_defaults"] = _load_store_guard(store_id)
        data["moves_by_sku"] = _load_price_war_moves(store_id, hours=72)
        data["decisions_by_sku"] = _load_guard_decisions(store_id)
    except Exception as e:
        logger.error(f"Не удалось загрузить данные защиты демпинга: {e}")
    return data


def _prepare_dumping_inputs(token: str, configs: dict, products: List[dict], store_id):
    """Синхронная подготовка цикла (merchant_id, мастер-группы, эффективные
    конфиги) — вызывается из run_price_dumping_job через asyncio.to_thread."""
    merchant_id = discover_merchant_id(token)
    if not merchant_id:
        return None, None, None

    from backend.state_helpers import _load_master_stock_groups
    master_groups = _load_master_stock_groups(store_id=store_id)

    # Индивидуальные конфиги + развёрнутые поверх них настройки активных
    # Мастер-Групп. Логика вынесена в config.build_effective_dumping_configs,
    # чтобы вкладка демпинга считала «включён ли демпинг» ровно тем же кодом.
    effective_configs, _cfg_sources = build_effective_dumping_configs(
        products, configs, master_groups
    )
    return merchant_id, master_groups, effective_configs


def _decide_and_persist(market_snapshot, effective_configs: dict, merchant_id: str, store_id):
    """Расчёт решений Мозга и все синхронные записи цикла в прежнем порядке.

    Возвращает (brain_result, new_asks); new_asks=None — вопросы защиты не
    сохранились (уведомления тогда не шлём, как и раньше при исключении).
    """
    guard_data = _load_guard_data(store_id)
    brain_result = BrainWorker.calculate_price_decisions(
        market_snapshot, effective_configs, merchant_id, guard_data=guard_data
    )
    update_queue = brain_result["update_queue"]
    analytics = brain_result["analytics"]

    # Сохраняем агрегированную аналитику в стейт (per-store, Слайс 5)
    try:
        from backend.state_helpers import _save_analytics_snapshot, _upsert_analytics_daily_history
        _save_analytics_snapshot(analytics, store_id=store_id)
        _upsert_analytics_daily_history(analytics, store_id=store_id)
    except Exception:
        pass

    # Piggyback: расклад демпинг-войны по каждому SKU + кто нас обходит —
    # из уже загруженных в этом цикле offers, без лишних запросов к Kaspi.
    try:
        from backend.state_helpers import _upsert_price_war_snapshot, _append_competitor_encounters
        _upsert_price_war_snapshot(brain_result.get("price_war_items", []), store_id=store_id)
        _append_competitor_encounters(brain_result.get("competitor_encounters", []), store_id=store_id)
    except Exception:
        pass

    new_asks = None
    # История ходов для защиты от лесенки и отчёта об атаках: каждое наше
    # изменение цены и соперник, на которого мы отвечали.
    if store_id is not None:
        try:
            from backend.state_helpers import _record_price_war_moves
            _record_price_war_moves([
                {
                    "sku": t["sku"],
                    "our_old": t["old_price"],
                    "our_new": t["new_price"],
                    "action": t["calculation_result"].get("action"),
                    "rival": t["calculation_result"].get("rival"),
                }
                for t in update_queue
            ] + list(brain_result.get("guard_events") or []), store_id=store_id)
        except Exception as e:
            logger.error(f"Не удалось записать ходы демпинга: {e}")

        try:
            from backend.state_helpers import _upsert_guard_asks
            new_asks = _upsert_guard_asks(brain_result.get("guard_asks") or [], store_id=store_id)
        except Exception as e:
            logger.error(f"Не удалось обработать вопросы/тревоги защиты демпинга: {e}")

    return brain_result, new_asks


async def run_price_dumping_job(
    token: str,
    configs: dict,
    products: List[dict],
    save_override_func,
    store_id=None
) -> dict:
    """
    Оркестратор трех воркеров (Radar -> Brain -> Executor).
    Сохраняет 100% обратную совместимость с API бэкенда.

    store_id=None (по умолчанию) — легаси-поведение, нескоуп-чтение мастер-групп
    остатка (как было всегда). store_id задан (Phase 7 per-store вызовы) —
    мастер-группы остатка фильтруются по стору, чтобы не подмешивать чужие.
    """
    merchant_id, master_groups, effective_configs = await asyncio.to_thread(
        _prepare_dumping_inputs, token, configs, products, store_id
    )
    if not merchant_id:
        # НЕ подставляем чужой merchant_id (раньше был хардкод "20186986" —
        # реальный ID Barkhan, из-за чего демпинг для любого другого магазина
        # с недоступным /pointofservices тихо работал под его личностью и
        # ловил 403 от Kaspi на каждом шаге). Честно пропускаем цикл для
        # этого стора — следующий тик может определить merchant_id успешно.
        logger.warning("Не удалось определить merchant_id — пропускаем цикл демпинга для этого магазина.")
        return {"success": False, "message": "Не удалось определить Merchant ID магазина (Kaspi API недоступен). Попробуйте позже.", "logs": []}

    # 1. 📡 Воркер-Радар
    market_snapshot = await RadarWorker.scan_market(products, effective_configs, store_id=store_id)
    if not market_snapshot:
        return {
            "success": True,
            "updated_count": 0,
            "message": "Нет товаров с включенным авто-демпингом.",
            "logs": []
        }

    # 2. 🧠 Воркер-Мозг + запись результатов. Всё синхронное (расчёт и
    # 6-7 записей в БД) — одним вызовом в пуле потоков: на event loop это
    # стоило ~2.2 с блокировки на магазин за цикл (py-spy на проде
    # 2026-10-17, из них _upsert_price_war_snapshot ~1.4 с).
    brain_result, new_asks = await asyncio.to_thread(
        _decide_and_persist, market_snapshot, effective_configs, merchant_id, store_id
    )
    update_queue = brain_result["update_queue"]
    analytics = brain_result["analytics"]
    calculation_logs = brain_result.get("calculation_logs", [])

    if store_id is not None and new_asks is not None:
        # Продавцы без рейтинга, о которых ещё не спрашивали менеджера, и
        # паузы после резкого падения цены — в Телеграм. Сбой уведомлений
        # не должен ронять цикл.
        try:
            from backend.dumper import guard_notify
            all_asks = brain_result.get("guard_asks") or []
            await guard_notify.notify_new_asks(store_id, new_asks, all_asks)
            await guard_notify.notify_drop_pauses(store_id, brain_result.get("guard_alerts") or [])
        except Exception as e:
            logger.error(f"Не удалось обработать вопросы/тревоги защиты демпинга: {e}")

    # Бродкаст аналитики в WebSocket
    from backend.dumper.telemetry import telemetry_manager
    await telemetry_manager.broadcast("analytics_update", analytics, store_id=store_id)

    # 3. 🚀 Воркер-Исполнитель
    # execute_updates — синхронная функция с последовательными сетевыми
    # запросами к Kaspi MC и time.sleep() джиттером/backoff'ом между ними
    # (до 60с при 429/403). Вызов без to_thread замораживал единственный
    # event loop приложения (--workers 1) на всё время выполнения очереди —
    # это и был периодический "сервис недоступен" каждые ~5 минут.
    exec_result = await asyncio.to_thread(
        RateLimitExecutorWorker.execute_updates,
        update_queue, save_override_func, merchant_id=merchant_id, store_id=store_id,
    )
    exec_result["analytics"] = analytics
    
    # Объединяем диагностические логи расчета и выполнения
    all_logs = list(calculation_logs) + list(exec_result.get("logs", []))
    exec_result["logs"] = all_logs
    return exec_result

__all__ = [
    "discover_merchant_id",
    "_decode_base64_if_needed",
    "DumpingProductConfig",
    "build_sku_alias_map",
    "build_effective_dumping_configs",
    "RadarWorker",
    "fetch_competitor_offers",
    "_fetch_offers_sync",
    "BrainWorker",
    "DumpingEngine",
    "RateLimitExecutorWorker",
    "generate_xml_feed",
    "run_price_dumping_job"
]
