"""«Автосборка» — оркестрация вокруг чистого движка правил
(auto_assembly_engine.py). Дёргается из run_packaging_sync_cycle
(stock_sync_worker.py, 30с тик, per-store) — второй независимый опрос
Kaspi заведён не был, используются те же fetch-функции, что и ручной путь
routers/orders.py::pack_all_orders.

Для сторов без единого включённого правила — ни одного лишнего запроса к
Kaspi (ранний выход после чтения правил из БД).
"""
import asyncio
import datetime
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set

from backend.kaspi_wrapper import KaspiAPI
from backend.state_helpers import _load_master_stock_groups, _get_sync_db_session
from backend.services.auto_assembly_engine import (
    AutoAssemblyRule,
    OrderSnapshot,
    evaluate_order,
)

logger = logging.getLogger(__name__)

SPOOL_DIR = Path(tempfile.gettempdir()) / "auto_assembly_spool"
SPOOL_DIR.mkdir(parents=True, exist_ok=True)


def _rule_from_row(row) -> AutoAssemblyRule:
    return AutoAssemblyRule(
        delivery_type=row.delivery_type,
        enabled=row.enabled,
        dry_run=row.dry_run,
        stage_accept=row.stage_accept,
        stage_assemble=row.stage_assemble,
        stage_waybill=row.stage_waybill,
        schedule=json.loads(row.schedule) if row.schedule else None,
        max_order_price=row.max_order_price,
        sku_blacklist=json.loads(row.sku_blacklist) if row.sku_blacklist else [],
        respect_stock_groups=row.respect_stock_groups,
        max_per_cycle=row.max_per_cycle,
    )


def _load_rules(store_id: str) -> Dict[str, AutoAssemblyRule]:
    session = _get_sync_db_session()
    if not session:
        return {}
    try:
        from sqlalchemy import select
        from backend.db.models import AutoAssemblyRuleDB
        rows = session.scalars(
            select(AutoAssemblyRuleDB).where(AutoAssemblyRuleDB.store_id == store_id)
        ).all()
        return {r.delivery_type: _rule_from_row(r) for r in rows}
    finally:
        session.close()


def _load_excluded_order_codes(store_id: str) -> Set[str]:
    """Заказы, уже отмеченные 'вне окна расписания' на прошлом тике — по
    явному выбору пользователя автоматика их больше не трогает никогда."""
    session = _get_sync_db_session()
    if not session:
        return set()
    try:
        from sqlalchemy import select
        from backend.db.models import AutoAssemblyLogDB
        rows = session.scalars(
            select(AutoAssemblyLogDB.order_code)
            .where(AutoAssemblyLogDB.store_id == store_id, AutoAssemblyLogDB.stage == "window")
            .distinct()
        ).all()
        return set(rows)
    finally:
        session.close()


def _last_skip_decision(session, store_id: str, order_code: str, stage: str) -> Optional[str]:
    from sqlalchemy import select
    from backend.db.models import AutoAssemblyLogDB
    row = session.scalars(
        select(AutoAssemblyLogDB)
        .where(
            AutoAssemblyLogDB.store_id == store_id,
            AutoAssemblyLogDB.order_code == order_code,
            AutoAssemblyLogDB.stage == stage,
        )
        .order_by(AutoAssemblyLogDB.id.desc())
        .limit(1)
    ).first()
    return row.decision if row else None


def _log(store_id: str, order_code: str, stage: str, decision: str, reason: str, dedupe_skips: bool = False):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import AutoAssemblyLogDB
        if dedupe_skips and decision.startswith("SKIP"):
            if _last_skip_decision(session, store_id, order_code, stage) == decision:
                return
        session.add(AutoAssemblyLogDB(
            store_id=store_id, order_code=order_code, stage=stage,
            decision=decision, reason=reason,
        ))
        session.commit()
    except Exception as e:
        logger.error(f"[AutoAssembly] Failed to write log entry: {e}")
        session.rollback()
    finally:
        session.close()


def _order_price(order: dict) -> float:
    try:
        return float(str(order.get("price", "0")).replace(" ₸", "").replace(" ", "").strip() or 0)
    except ValueError:
        return 0.0


def _order_skus(order: dict) -> List[str]:
    return [str(e.get("sku") or e.get("merchantSku") or "") for e in order.get("entries", [])]


def _stock_ok(order: dict, groups: dict) -> bool:
    """True если хотя бы один товар заказа не привязан ни к одной
    Мастер-Группе (нечего проверять), либо привязанная группа имеет
    остаток > 0. Совпадает с логикой сопоставления в
    stock_sync_worker.py::process_packaging_auto_deduction."""
    if not groups:
        return True
    for sku in _order_skus(order):
        sku_lower = sku.strip().lower()
        if not sku_lower:
            continue
        for g in groups.values():
            linked = set(str(s).strip().lower() for s in g.get("linked_skus", []))
            if sku_lower in linked or any(s in sku_lower or sku_lower.startswith(s) for s in linked if len(s) >= 4):
                if int(g.get("shared_stock", 0)) <= 0:
                    return False
    return True


def _snapshot(order: dict) -> OrderSnapshot:
    return OrderSnapshot(
        code=str(order.get("id")),
        delivery_type=order.get("deliveryType") or "Kaspi Доставка",
        price=_order_price(order),
        skus=_order_skus(order),
    )


async def _notify_new_order(store_id: str, order: dict):
    """Уведомление money-группы (Ф4, docs/TELEGRAM_BOT_PLAN.md §6): заказ
    автоматически принят движком автосборки. Express — всегда urgent
    (обходит батчинг и тихие часы), остальные проверяются на порог суммы
    per-recipient уже внутри notify_dispatch.dispatch (prefs.new_order_min_sum
    здесь не фильтруется централизованно — упрощение, см. HANDOFF)."""
    try:
        from backend.chatbot import notify_dispatch
        snap = _snapshot(order)
        code = str(order.get("id"))
        is_express = snap.delivery_type == "Express"
        icon = "⚡" if is_express else "🆕"
        text = f"{icon} Заказ #{code} ({snap.delivery_type}) принят автоматически, сумма {snap.price:,.0f} ₸".replace(",", " ")
        await notify_dispatch.dispatch(store_id, "money", text, urgent=is_express, amount=snap.price, card="new_order")
    except Exception as e:
        logger.error(f"[AutoAssembly] Не удалось отправить уведомление о новом заказе: {e}")


async def _notify_stage_failed(store_id: str, stage: str, order_code: str, error: str):
    try:
        from backend.chatbot import notify_dispatch
        text = f"🚨 Автосборка: этап «{stage}» упал на заказе #{order_code}\n{error[:300]}"
        await notify_dispatch.dispatch(
            store_id, "problems", text, urgent=True,
            dedup_key=f"aa_fail:{stage}:{order_code}", card="assembly_failed",
        )
    except Exception as e:
        logger.error(f"[AutoAssembly] Не удалось отправить уведомление об ошибке этапа: {e}")


async def _evaluate_and_log(
    store_id: str, order: dict, stage: str, rules: Dict[str, AutoAssemblyRule], excluded: Set[str],
    groups: dict, now: datetime.datetime, per_rule_counts: Dict[str, int],
):
    snap = _snapshot(order)
    rule = rules.get(snap.delivery_type)
    if rule is None or not rule.enabled:
        return None  # не настроено для этого вида доставки — не спамим лог

    stock_ok = True if not (rule.respect_stock_groups) else await asyncio.to_thread(_stock_ok, order, groups)
    already_excluded = snap.code in excluded
    count_so_far = per_rule_counts.get(snap.delivery_type, 0)

    result = evaluate_order(
        snap, rule, now,
        already_excluded=already_excluded,
        stock_ok=stock_ok,
        orders_processed_this_cycle=count_so_far,
    )

    if result.decision == "SKIP_OUT_OF_WINDOW":
        await asyncio.to_thread(_log, store_id, snap.code, "window", result.decision, result.reason)
        return None
    if result.decision != "PROCEED":
        await asyncio.to_thread(_log, store_id, snap.code, stage, result.decision, result.reason, True)
        return None
    if stage not in result.allowed_stages:
        return None

    per_rule_counts[snap.delivery_type] = count_so_far + 1
    return result


async def _run_accept_stage(token: str, store_id: str, rules, excluded, groups, now, per_rule_counts):
    if not any(r.enabled and r.stage_accept for r in rules.values()):
        return
    try:
        orders = await asyncio.to_thread(
            KaspiAPI.get_orders_with_products, token, "NEW", "APPROVED_BY_BANK", 50
        )
    except Exception as e:
        logger.error(f"[AutoAssembly] Accept stage fetch failed for store {store_id}: {e}")
        return

    for order in orders:
        decision = await _evaluate_and_log(store_id, order, "accept", rules, excluded, groups, now, per_rule_counts)
        if decision is None:
            continue
        code = str(order.get("id"))
        internal_id = order.get("internalId")
        if decision.dry_run:
            await asyncio.to_thread(_log, store_id, code, "accept", "DONE", "Сухой прогон: заказ был бы принят")
            continue
        try:
            await asyncio.to_thread(KaspiAPI.update_order_status, token, internal_id, "ACCEPTED_BY_MERCHANT")
            await asyncio.to_thread(_log, store_id, code, "accept", "DONE", "Заказ автоматически принят")
            await _notify_new_order(store_id, order)
        except Exception as e:
            await asyncio.to_thread(_log, store_id, code, "accept", "FAILED", str(e))
            await _notify_stage_failed(store_id, "accept", code, str(e))


async def _run_assemble_stage(token: str, store_id: str, rules, excluded, groups, now, per_rule_counts):
    if not any(r.enabled and r.stage_assemble for r in rules.values()):
        return
    try:
        orders = await asyncio.to_thread(
            KaspiAPI.get_orders_with_products, token, "KASPI_DELIVERY", "ACCEPTED_BY_MERCHANT", 50, False
        )
    except Exception as e:
        logger.error(f"[AutoAssembly] Assemble stage fetch failed for store {store_id}: {e}")
        return

    for order in orders:
        decision = await _evaluate_and_log(store_id, order, "assemble", rules, excluded, groups, now, per_rule_counts)
        if decision is None:
            continue
        code = str(order.get("id"))
        qty = order.get("quantity", 1)
        if decision.dry_run:
            await asyncio.to_thread(_log, store_id, code, "assemble", "DONE", "Сухой прогон: заказ был бы упакован")
            continue
        try:
            await asyncio.to_thread(KaspiAPI.assemble_cargo, token, code, qty, store_id)
            await asyncio.to_thread(_log, store_id, code, "assemble", "DONE", "Заказ автоматически упакован")
        except Exception as e:
            await asyncio.to_thread(_log, store_id, code, "assemble", "FAILED", str(e))
            await _notify_stage_failed(store_id, "assemble", code, str(e))


def _already_spooled(store_id: str, order_code: str) -> bool:
    session = _get_sync_db_session()
    if not session:
        return False
    try:
        from backend.db.models import WaybillSpoolDB
        return session.get(WaybillSpoolDB, (store_id, order_code)) is not None
    finally:
        session.close()


def _save_spool_row(store_id: str, order_code: str, file_path: str):
    session = _get_sync_db_session()
    if not session:
        return
    try:
        from backend.db.models import WaybillSpoolDB
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if not session.get(WaybillSpoolDB, (store_id, order_code)):
            session.add(WaybillSpoolDB(
                store_id=store_id, order_code=order_code, date=today, file_path=file_path,
            ))
            session.commit()
    except Exception as e:
        logger.error(f"[AutoAssembly] Failed to persist spool row: {e}")
        session.rollback()
    finally:
        session.close()


async def _run_waybill_stage(token: str, store_id: str, rules, excluded, groups, now, per_rule_counts):
    if not any(r.enabled and r.stage_waybill for r in rules.values()):
        return
    try:
        orders = await asyncio.to_thread(
            KaspiAPI.get_orders_with_products, token, "KASPI_DELIVERY", "ACCEPTED_BY_MERCHANT", 50, True
        )
    except Exception as e:
        logger.error(f"[AutoAssembly] Waybill stage fetch failed for store {store_id}: {e}")
        return

    for order in orders:
        if order.get("isTransmitted"):
            continue
        code = str(order.get("id"))
        if await asyncio.to_thread(_already_spooled, store_id, code):
            continue

        decision = await _evaluate_and_log(store_id, order, "waybill", rules, excluded, groups, now, per_rule_counts)
        if decision is None:
            continue

        waybill_url = order.get("waybillUrl")
        if not waybill_url:
            continue

        if decision.dry_run:
            await asyncio.to_thread(_log, store_id, code, "waybill", "DONE", "Сухой прогон: накладная была бы скачана")
            continue

        try:
            resp = await asyncio.to_thread(KaspiAPI._get, waybill_url, token, None)
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")
            store_dir = SPOOL_DIR / store_id
            store_dir.mkdir(parents=True, exist_ok=True)
            file_path = store_dir / f"{code}_{uuid.uuid4().hex[:8]}.pdf"
            file_path.write_bytes(resp.content)
            await asyncio.to_thread(_save_spool_row, store_id, code, str(file_path))
            await asyncio.to_thread(_log, store_id, code, "waybill", "DONE", "Накладная скачана в пачку на печать")
        except Exception as e:
            await asyncio.to_thread(_log, store_id, code, "waybill", "FAILED", str(e))
            await _notify_stage_failed(store_id, "waybill", code, str(e))


async def run_auto_assembly_cycle(token: str, store_id: str):
    """Один цикл автосборки для одного стора. Вызывается из
    run_packaging_sync_cycle (stock_sync_worker.py) на каждый активный стор."""
    rules = await asyncio.to_thread(_load_rules, store_id)
    if not any(r.enabled for r in rules.values()):
        return

    excluded = await asyncio.to_thread(_load_excluded_order_codes, store_id)
    groups = await asyncio.to_thread(_load_master_stock_groups, store_id)
    now = datetime.datetime.now()
    per_rule_counts: Dict[str, int] = {}

    await _run_accept_stage(token, store_id, rules, excluded, groups, now, per_rule_counts)
    await _run_assemble_stage(token, store_id, rules, excluded, groups, now, per_rule_counts)
    await _run_waybill_stage(token, store_id, rules, excluded, groups, now, per_rule_counts)


# ── Живая пачка накладных ─────────────────────────────────────────────
# Склейка (категоризация/раскладка/аудит геометрии) намеренно НЕ происходит
# при каждом скачивании накладной — только по клику пользователя
# («копить и ждать кнопку», решение пользователя при планировании фичи).
# Хранилище — WaybillSpoolDB, склейка идёт через уже существующий PDF-пайплайн
# print_batcher_service.process_zip_or_pdfs (пачка PDF заворачивается в ZIP
# в памяти, дальше — тот же код, что и для ручной загрузки в PrintBatcher).

def get_unprinted_spool_rows(store_id: str) -> List[dict]:
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        from sqlalchemy import select
        from backend.db.models import WaybillSpoolDB
        rows = session.scalars(
            select(WaybillSpoolDB).where(
                WaybillSpoolDB.store_id == store_id,
                WaybillSpoolDB.printed_at.is_(None),
            )
        ).all()
        return [
            {"order_code": r.order_code, "date": r.date, "file_path": r.file_path, "downloaded_at": r.downloaded_at}
            for r in rows
        ]
    finally:
        session.close()


def build_spool_zip_bytes(store_id: str) -> bytes:
    """Заворачивает все ещё не напечатанные накладные стора в ZIP-архив в
    памяти — на вход print_batcher_service.process_zip_or_pdfs, который уже
    умеет категоризировать/раскладывать/дедуплицировать PDF из ZIP."""
    import io
    import zipfile

    rows = get_unprinted_spool_rows(store_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            path = Path(row["file_path"])
            if path.exists():
                zf.write(path, arcname=f"{row['order_code']}.pdf")
    return buf.getvalue()


def mark_spool_printed(store_id: str, order_codes: Optional[List[str]] = None):
    """Отмечает накладные напечатанными и параллельно пишет в
    PrintHistoryDB — чтобы будущая РУЧНАЯ загрузка того же ZIP в
    PrintBatcher тоже корректно дедуплицировала эти заказы."""
    session = _get_sync_db_session()
    if not session:
        return 0
    try:
        from sqlalchemy import select
        from backend.db.models import WaybillSpoolDB
        from backend.services.print_batcher_service import save_printed_history

        stmt = select(WaybillSpoolDB).where(
            WaybillSpoolDB.store_id == store_id,
            WaybillSpoolDB.printed_at.is_(None),
        )
        if order_codes:
            stmt = stmt.where(WaybillSpoolDB.order_code.in_(order_codes))
        rows = session.scalars(stmt).all()

        now = datetime.datetime.now()
        history_keys = set()
        for r in rows:
            r.printed_at = now
            history_keys.add(f"order_{r.order_code}")
        session.commit()

        if history_keys:
            save_printed_history(history_keys, store_id=store_id, date_str=now.strftime("%Y-%m-%d"))
        return len(rows)
    except Exception as e:
        logger.error(f"[AutoAssembly] Failed to mark spool printed: {e}")
        session.rollback()
        return 0
    finally:
        session.close()
