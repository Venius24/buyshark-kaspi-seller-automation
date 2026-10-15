"""Store-scoped proposals. Caller owns transaction; no catalog/price writes."""
import json
import math
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select, update, func

from backend.assistant.config import max_pending_proposals, price_gateway_name
from backend.db.models import (AssistantPriceProposalDB as Proposal, AssistantConversationDB,
                               Store, StoreProductDB, DumpingConfigDB)
from backend.kaspi_offer_price import extract_offer_price
from backend.state_helpers import dumping_config_for_product


class ProposalError(ValueError):
    pass


class ProposalNotFound(ProposalError):
    pass


class ProposalConflict(ProposalError):
    pass


class PriceGateway(Protocol):
    async def apply(self, proposal: Proposal) -> dict: ...


class DryRunGateway:
    async def apply(self, proposal: Proposal) -> dict:
        return {"gateway": "dry_run", "price_changed": False, "sku": proposal.sku,
                "new_price": proposal.new_price, "message": "Dry run: цена НЕ изменена"}


def get_gateway() -> PriceGateway:
    price_gateway_name()  # Fail closed for every other configuration.
    return DryRunGateway()


def _manager(ctx):
    if ctx.role not in ("MANAGER", "OWNER"):
        raise PermissionError("Требуется роль MANAGER или OWNER")


async def validate_price(db, ctx, sku, new_price):
    # Executor/extract_offer_price use whole tenge. Reject fractions rather than
    # silently truncating a price that a human is about to approve.
    if (isinstance(new_price, bool) or not isinstance(new_price, (int, float))
            or new_price <= 0 or new_price > 9223372036854775807
            or not math.isfinite(new_price) or new_price != int(new_price)):
        raise ProposalError("Цена должна быть положительным целым числом тенге")
    catalog = (await db.execute(select(StoreProductDB.payload).where(
        StoreProductDB.store_id == ctx.store_id))).scalar_one_or_none()
    products = json.loads(catalog) if catalog else []
    matches = [p for p in products if sku == p.get("merchantSku")]
    if not matches:
        matches = [p for p in products if sku == p.get("sku")]
    if len(matches) != 1:
        raise ProposalError("SKU не найден или неоднозначен в каталоге магазина")
    product = matches[0]
    rows = (await db.execute(select(DumpingConfigDB).where(
        DumpingConfigDB.store_id == ctx.store_id).execution_options(populate_existing=True))).scalars().all()
    configs = {r.sku: {"min_price": r.min_price, "max_price": r.max_price} for r in rows}
    cfg = dumping_config_for_product(product, configs) or {}
    minimum = cfg.get("min_price")
    maximum = cfg.get("max_price")
    if minimum is None or not math.isfinite(minimum) or minimum <= 0:
        raise ProposalError("Минимальная цена в настройках демпинга не задана")
    if maximum is not None and (not math.isfinite(maximum) or maximum < 0):
        raise ProposalError("Некорректная максимальная цена в настройках демпинга")
    maximum = maximum or None  # Existing dumping config: 0 means no maximum.
    if new_price < minimum:
        raise ProposalError("Цена ниже минимальной цены товара")
    if maximum is not None and new_price > maximum:
        raise ProposalError("Цена выше максимальной цены товара")
    current = extract_offer_price({"price": product.get("price_num")}) or extract_offer_price(product)
    if current is None:
        raise ProposalError("Текущая цена товара неизвестна")
    if new_price == current:
        raise ProposalError("Цена равна текущей цене товара")
    return product.get("merchantSku") or sku, current, minimum, maximum


async def create_proposal(db, ctx, sku, new_price, reason):
    _manager(ctx)
    if not isinstance(sku, str) or not sku.strip() or len(sku) > 100:
        raise ProposalError("SKU должен содержать от 1 до 100 символов")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
        raise ProposalError("Причина должна содержать от 1 до 300 символов")
    # Serialize creators per store, including the empty pending set. An UPDATE
    # obtains a row lock on PostgreSQL and a writer lock on SQLite until commit.
    locked = await db.execute(update(Store).where(Store.id == ctx.store_id).values(id=Store.id))
    if locked.rowcount != 1:
        raise ProposalError("Магазин не найден")
    count = (await db.execute(select(func.count()).select_from(Proposal).where(
        Proposal.store_id == ctx.store_id, Proposal.status == "pending"))).scalar_one()
    if count >= max_pending_proposals():
        raise ProposalError("Достигнут лимит pending-предложений магазина")
    conversation_id = getattr(ctx, "conversation_id", None)
    if conversation_id and not (await db.execute(select(AssistantConversationDB.id).where(
            AssistantConversationDB.id == conversation_id,
            AssistantConversationDB.store_id == ctx.store_id))).scalar_one_or_none():
        raise ProposalError("Диалог магазина не найден")
    sku, current, minimum, maximum = await validate_price(db, ctx, sku, new_price)
    row = Proposal(store_id=ctx.store_id, conversation_id=conversation_id, sku=sku,
                   old_price=current, new_price=int(new_price), min_price_at_creation=minimum,
                   max_price_at_creation=maximum, reason=reason.strip(), created_by_user_id=ctx.user_id)
    db.add(row)
    await db.flush()
    return row


async def list_proposals(db, ctx, status=None):
    if status is not None and status not in ("pending", "approved", "rejected", "failed"):
        raise ProposalError("Неизвестный статус предложения")
    stmt = select(Proposal).where(Proposal.store_id == ctx.store_id)
    if status is not None:
        stmt = stmt.where(Proposal.status == status)
    return list((await db.execute(stmt.order_by(Proposal.created_at.desc(), Proposal.id).limit(200))).scalars())


async def get_proposal(db, ctx, proposal_id):
    row = (await db.execute(select(Proposal).where(
        Proposal.id == proposal_id, Proposal.store_id == ctx.store_id)
        .execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None:
        raise ProposalNotFound("Предложение не найдено")
    return row


async def _decide(db, ctx, proposal_id, status):
    _manager(ctx)
    result = await db.execute(update(Proposal).where(
        Proposal.id == proposal_id, Proposal.store_id == ctx.store_id, Proposal.status == "pending"
    ).values(status=status, decided_at=datetime.now(timezone.utc).replace(tzinfo=None), decided_by_user_id=ctx.user_id))
    if result.rowcount != 1:
        await get_proposal(db, ctx, proposal_id)
        raise ProposalConflict("Предложение уже рассмотрено")
    return await get_proposal(db, ctx, proposal_id)


async def approve_proposal(db, ctx, proposal_id, gateway):
    row = await _decide(db, ctx, proposal_id, "approved")
    try:
        await validate_price(db, ctx, row.sku, row.new_price)
    except ProposalError as exc:
        row.status = "rejected"
        row.apply_result = json.dumps({"error": str(exc), "price_changed": False}, ensure_ascii=False)
    else:
        try:
            row.apply_result = json.dumps(await gateway.apply(row), ensure_ascii=False)
        except Exception as exc:
            row.status = "failed"
            row.apply_result = json.dumps({"error": str(exc)[:500], "price_changed": False}, ensure_ascii=False)
    await db.flush()
    return row


async def reject_proposal(db, ctx, proposal_id):
    return await _decide(db, ctx, proposal_id, "rejected")


def proposal_out(row):
    return {key: (value.isoformat() if isinstance(value, datetime) else
                  json.loads(value) if key == "apply_result" and value else value)
            for key in Proposal.__table__.columns.keys() for value in [getattr(row, key)]}
