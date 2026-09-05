"""Подсчёт товаров против лимита тарифа. Считаются ТОЛЬКО активные в продаже
(product["status"] == "Активен", см. backend/kaspi_wrapper.py:1051) — снятые
с продажи и архивные не идут в лимит (решение владельца, 2026-08-12).

Возвращает None, если состояние неизвестно (StoreProductDB ещё нет, или
устарело дольше суток) — неизвестно НЕ значит "соответствует лимиту" и НЕ
значит "нарушение", это просто "нельзя судить сейчас". Новый магазин читает
0 товаров до первой синхронизации (products_sync_worker, тик 1800с) — его
нельзя блокировать по пустому кэшу.
"""
import datetime as _dt
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_STALE_AFTER_HOURS = 24


def count_billable_products(store_id: str) -> Optional[int]:
    from backend.state_helpers import _get_sync_db_session, _load_products
    from backend.db.models import StoreProductDB

    session = _get_sync_db_session()
    if not session:
        return None
    try:
        row = session.get(StoreProductDB, store_id)
        if not row:
            return None
        updated_at = getattr(row, "updated_at", None)
        if updated_at and (_dt.datetime.utcnow() - updated_at) > _dt.timedelta(hours=_STALE_AFTER_HOURS):
            return None
    finally:
        session.close()

    products = _load_products(store_id)
    return sum(1 for p in products if p.get("status") == "Активен")


def employee_seat_limit(store_id: str) -> Optional[int]:
    """Сколько сотрудников (кроме владельца) разрешает тариф магазина.

    None = безлимит (DEV/ГигаПредприятие). В отличие от товаров, лимит мест
    НЕ блокирует магазин задним числом: понижение тарифа не выкидывает уже
    заведённых людей посреди рабочего дня (это ломало бы сборку заказов из-за
    вопроса оплаты), оно лишь закрывает добавление новых — проверка стоит на
    POST /api/v1/employees. Поэтому здесь нет ветки engine.py, только чтение.
    """
    from backend.billing import catalog
    from backend.db.models import StoreTariffDB
    from backend.state_helpers import _get_sync_db_session

    session = _get_sync_db_session()
    if not session:
        # БД недоступна — не выдаём щедрый безлимит и не врём про 0.
        # Вызывающий (create_employee) трактует None как «без ограничений»,
        # поэтому здесь безопаснее вернуть лимит самого дешёвого платного
        # плана, чем открыть добавление всем.
        logger.warning("employee_seat_limit: нет сессии БД, отдаём консервативный лимит")
        return catalog.PLANS["START"].max_employees

    try:
        row = session.get(StoreTariffDB, store_id)
        plan_code = row.plan_code if row else catalog.DEFAULT_PLAN_CODE
    finally:
        session.close()

    plan = catalog.get_plan(plan_code)
    return plan.max_employees
