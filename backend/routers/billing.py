"""Read-only API кабинета магазина (Слайс 2): текущий тариф, баланс,
расход дневных кредитов, публичный прайс-лист, история транзакций. Никаких
мутаций — те живут в admin_billing.py (только суперадмин)."""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.db.models import StoreTariffDB, StoreBalanceDB
from backend.security.store_context import StoreContext, get_store_context
from backend.billing import catalog, credits as billing_credits, ledger as billing_ledger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["Billing"])


class PlanOut(BaseModel):
    code: str
    name: str
    max_products: Optional[int]
    monthly_price_minor: Optional[int]
    badge: Optional[str]


class BillingSummaryOut(BaseModel):
    plan_code: str
    plan_name: str
    max_products: Optional[int]
    monthly_price_minor: int
    credit_addon_code: str
    billing_status: str
    blocked_reason: Optional[str]
    balance_minor: int
    block_threshold_minor: int
    period_end: Optional[str]
    prompt_band: str
    prompt_multiplier_milli: int
    credits_used_milli: int
    credits_limit_milli: int


@router.get("/summary", response_model=BillingSummaryOut)
async def get_billing_summary(
    ctx: StoreContext = Depends(get_store_context),
    db: AsyncSession = Depends(get_db),
):
    tariff = await db.get(StoreTariffDB, ctx.store_id)
    balance_row = await db.get(StoreBalanceDB, ctx.store_id)

    plan_code = tariff.plan_code if tariff else catalog.DEFAULT_PLAN_CODE
    plan = catalog.get_plan(plan_code)
    credit_addon_code = tariff.credit_addon_code if tariff else catalog.DEFAULT_CREDIT_ADDON

    usage = await asyncio.to_thread(billing_credits.today_usage, ctx.store_id)

    from backend.chatbot.prompt import get_profile
    profile = await asyncio.to_thread(get_profile, ctx.store_id)
    band_code = profile.get("prompt_tier") or catalog.DEFAULT_PROMPT_BAND

    return BillingSummaryOut(
        plan_code=plan.code,
        plan_name=plan.name,
        max_products=plan.max_products,
        monthly_price_minor=catalog.plan_price_minor(plan_code, tariff.custom_monthly_price_minor if tariff else None),
        credit_addon_code=credit_addon_code,
        billing_status=tariff.billing_status if tariff else "ACTIVE",
        blocked_reason=tariff.blocked_reason if tariff else None,
        balance_minor=balance_row.balance_minor if balance_row else 0,
        block_threshold_minor=catalog.BALANCE_BLOCK_THRESHOLD_MINOR,
        period_end=tariff.period_end.isoformat() if tariff and tariff.period_end else None,
        prompt_band=band_code,
        prompt_multiplier_milli=catalog.multiplier_milli(band_code),
        credits_used_milli=usage.used_milli,
        credits_limit_milli=usage.limit_milli,
    )


@router.get("/plans", response_model=list[PlanOut])
async def get_billing_plans():
    return [
        PlanOut(code=p.code, name=p.name, max_products=p.max_products, monthly_price_minor=p.monthly_price_minor, badge=p.badge)
        for p in catalog.public_plans()
    ]


@router.get("/transactions")
async def get_billing_transactions(
    limit: int = 50,
    offset: int = 0,
    ctx: StoreContext = Depends(get_store_context),
):
    return await asyncio.to_thread(billing_ledger.list_transactions, ctx.store_id, limit, offset)
