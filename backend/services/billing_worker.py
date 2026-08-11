"""Биллинг-воркер: продление подписки и оценка блокировки по всем магазинам.

ЛОВУШКА (см. план тарификации): читает таблицу `stores` НАПРЯМУЮ через sync
сессию, а НЕ через `security.store_context.list_active_stores()`. Тот хелпер
фильтрует `status != 'PAUSED'` и молча выкидывает магазины с нерасшифровываемым
`encrypted_api_key` — такой магазин навсегда перестал бы платить абонентку
без единой строки в логе. Списание денег не должно зависеть от того же
фильтра, что решает, пускать ли воркер чат-бота.

Порядок за тик load-bearing: сначала продление (charge_period), потом оценка
блокировки (evaluate_block) — иначе магазин мог бы словить блок за баланс
за секунду до списания, которое вывело бы его в плюс.
"""
import asyncio
import logging

from backend.billing import catalog, engine as billing_engine

logger = logging.getLogger(__name__)

_TICK_SECONDS = 60


def _list_all_store_ids() -> list[str]:
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import Store

    session = _get_sync_db_session()
    if not session:
        return []
    try:
        return [row[0] for row in session.query(Store.id).all()]
    finally:
        session.close()


def _plan_code_for(store_id: str) -> str:
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreTariffDB

    session = _get_sync_db_session()
    if not session:
        return catalog.DEFAULT_PLAN_CODE
    try:
        tariff = session.get(StoreTariffDB, store_id)
        return tariff.plan_code if tariff else catalog.DEFAULT_PLAN_CODE
    finally:
        session.close()


async def run_billing_cycle():
    """Один проход по ВСЕМ магазинам (не только 'активным'). DEV/бесплатные
    планы хардскипаются целиком — не списываются, не блокируются, не
    лимитируются (главный предохранитель миграции 027)."""
    store_ids = await asyncio.to_thread(_list_all_store_ids)
    for store_id in store_ids:
        try:
            plan_code = await asyncio.to_thread(_plan_code_for, store_id)
            if catalog.is_free_plan(plan_code):
                continue
            await asyncio.to_thread(billing_engine.charge_period, store_id)
            await asyncio.to_thread(billing_engine.evaluate_block, store_id)
        except Exception as e:
            logger.error(f"❌ [Billing] Ошибка обработки магазина {store_id}: {e}")


async def start_billing_loop():
    logger.info(f"💳 [Billing] Воркер тарификации запущен (тик {_TICK_SECONDS}s).")
    await asyncio.sleep(15)
    while True:
        try:
            await run_billing_cycle()
        except Exception as e:
            logger.error(f"❌ [Billing] Ошибка в фоновом цикле: {e}")
        await asyncio.sleep(_TICK_SECONDS)
