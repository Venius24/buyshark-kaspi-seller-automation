"""Суперадминский биллинг: список магазинов с тарифом/балансом, ручная
смена тарифа, пополнение/корректировка баланса, привязка реферера,
пересчёт блокировки, список транзакций, реестр рефереров и их начислений.
Зависимость require_superadmin висит на самом APIRouter — её нельзя забыть
на новом эндпоинте (Слайс 4/7)."""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.db.database import get_db
from backend.db.models import (
    User, Store, UserStore, StoreTariffDB, StoreBalanceDB,
    ReferralProfileDB, StoreReferralDB, ReferralAccrualDB,
)
from backend.security.platform_roles import require_superadmin
from backend.billing import catalog, ledger as billing_ledger, engine as billing_engine, referrals as billing_referrals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin Billing"], dependencies=[Depends(require_superadmin)])


class TariffUpdateRequest(BaseModel):
    plan_code: str
    credit_addon_code: Optional[str] = None
    custom_monthly_price_minor: Optional[int] = None


class TopupRequest(BaseModel):
    amount_minor: int
    idempotency_key: str
    comment: Optional[str] = None


class AdjustRequest(BaseModel):
    amount_minor: int  # знаковое: + начисление, - списание
    idempotency_key: str
    comment: Optional[str] = None


class ReferrerAssignRequest(BaseModel):
    referrer_user_id: str


class ReferrerCreateRequest(BaseModel):
    user_id: str
    commission_percent: int = 50
    commission_months: int = 3


@router.get("/stores")
async def list_stores(db: AsyncSession = Depends(get_db)):
    stores_res = await db.execute(select(Store))
    stores = stores_res.scalars().all()

    out = []
    for store in stores:
        tariff = await db.get(StoreTariffDB, store.id)
        balance_row = await db.get(StoreBalanceDB, store.id)
        referral = await db.get(StoreReferralDB, store.id)
        owner_res = await db.execute(
            select(User.email).join(UserStore, User.id == UserStore.user_id)
            .where(UserStore.store_id == store.id, UserStore.role == "OWNER").limit(1)
        )
        owner_email = owner_res.scalar_one_or_none()
        plan = catalog.get_plan(tariff.plan_code if tariff else catalog.DEFAULT_PLAN_CODE)
        out.append({
            "store_id": store.id,
            "name": store.name,
            "merchant_id": store.merchant_id,
            "owner_email": owner_email,
            "plan_code": plan.code,
            "plan_name": plan.name,
            "billing_status": tariff.billing_status if tariff else "ACTIVE",
            "balance_minor": balance_row.balance_minor if balance_row else 0,
            "credit_addon_code": tariff.credit_addon_code if tariff else catalog.DEFAULT_CREDIT_ADDON,
            "next_charge_at": tariff.next_charge_at.isoformat() if tariff and tariff.next_charge_at else None,
            "referrer_user_id": referral.referrer_user_id if referral else None,
        })
    return out


@router.get("/stores/{store_id}")
async def get_store_detail(store_id: str, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    tariff = await db.get(StoreTariffDB, store_id)
    balance_row = await db.get(StoreBalanceDB, store_id)
    referral = await db.get(StoreReferralDB, store_id)
    transactions = await asyncio.to_thread(billing_ledger.list_transactions, store_id, 50, 0)
    return {
        "store_id": store.id,
        "name": store.name,
        "merchant_id": store.merchant_id,
        "tariff": {
            "plan_code": tariff.plan_code if tariff else catalog.DEFAULT_PLAN_CODE,
            "credit_addon_code": tariff.credit_addon_code if tariff else catalog.DEFAULT_CREDIT_ADDON,
            "custom_monthly_price_minor": tariff.custom_monthly_price_minor if tariff else None,
            "billing_status": tariff.billing_status if tariff else "ACTIVE",
            "blocked_reason": tariff.blocked_reason if tariff else None,
            "period_start": tariff.period_start.isoformat() if tariff and tariff.period_start else None,
            "period_end": tariff.period_end.isoformat() if tariff and tariff.period_end else None,
            "next_charge_at": tariff.next_charge_at.isoformat() if tariff and tariff.next_charge_at else None,
        },
        "balance_minor": balance_row.balance_minor if balance_row else 0,
        "referrer_user_id": referral.referrer_user_id if referral else None,
        "transactions": transactions,
    }


@router.post("/stores/{store_id}/tariff")
async def set_store_tariff(store_id: str, req: TariffUpdateRequest, db: AsyncSession = Depends(get_db)):
    if req.plan_code not in catalog.PLANS:
        raise HTTPException(status_code=400, detail="Неизвестный код тарифа")
    if req.credit_addon_code and req.credit_addon_code not in catalog.CREDIT_TIERS:
        raise HTTPException(status_code=400, detail="Неизвестный код надбавки кредитов")

    tariff = await db.get(StoreTariffDB, store_id)
    if not tariff:
        raise HTTPException(status_code=404, detail="У магазина ещё нет тарифной строки (см. миграцию/подключение)")

    was_free = catalog.is_free_plan(tariff.plan_code)
    now = datetime.utcnow()

    tariff.plan_code = req.plan_code
    if req.credit_addon_code:
        tariff.credit_addon_code = req.credit_addon_code
    tariff.custom_monthly_price_minor = req.custom_monthly_price_minor
    tariff.updated_at = now

    # Первый переход с DEV/бесплатного плана на платный — здесь начинается
    # реальный отсчёт для продления (period_start/next_charge_at), и это же
    # activated_at, если реферальное окно ещё не материализовано.
    if was_free and not catalog.is_free_plan(req.plan_code):
        tariff.activated_at = now
        tariff.period_start = now
        tariff.period_end = now + timedelta(days=31)
        tariff.next_charge_at = tariff.period_end
        tariff.billing_anchor_day = now.day

    await db.commit()

    from backend.billing import enforcement
    enforcement.bust_cache(store_id)
    await asyncio.to_thread(billing_engine.evaluate_block, store_id)

    return {"ok": True}


@router.post("/stores/{store_id}/topup")
async def topup_store(store_id: str, req: TopupRequest, user: User = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    result = await asyncio.to_thread(
        billing_ledger.credit, store_id, req.amount_minor, "topup_manual",
        req.idempotency_key, comment=req.comment, created_by_user_id=user.id,
    )
    if not result.applied and result.reason != "already_applied":
        raise HTTPException(status_code=400, detail=result.reason or "Не удалось провести пополнение")
    await asyncio.to_thread(billing_engine.evaluate_block, store_id)
    return {"ok": True, "balance_minor": result.balance_minor}


@router.post("/stores/{store_id}/adjust")
async def adjust_store_balance(store_id: str, req: AdjustRequest, user: User = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    fn = billing_ledger.credit if req.amount_minor >= 0 else billing_ledger.debit
    result = await asyncio.to_thread(
        fn, store_id, abs(req.amount_minor), "adjustment",
        req.idempotency_key, comment=req.comment, created_by_user_id=user.id,
    )
    if not result.applied and result.reason != "already_applied":
        raise HTTPException(status_code=400, detail=result.reason or "Не удалось провести корректировку")
    await asyncio.to_thread(billing_engine.evaluate_block, store_id)
    return {"ok": True, "balance_minor": result.balance_minor}


@router.post("/stores/{store_id}/referrer")
async def assign_referrer(store_id: str, req: ReferrerAssignRequest, user: User = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    profile = await db.get(ReferralProfileDB, req.referrer_user_id)
    if not profile or not profile.is_active:
        raise HTTPException(status_code=400, detail="Пользователь не зарегистрирован как активный реферер")

    tariff = await db.get(StoreTariffDB, store_id)
    now = datetime.utcnow()
    window_start = tariff.activated_at if (tariff and tariff.activated_at) else now

    existing = await db.get(StoreReferralDB, store_id)
    if existing:
        existing.referrer_user_id = req.referrer_user_id
        existing.source = "manual"
        existing.assigned_by_user_id = user.id
        existing.window_start = window_start
        existing.window_end = window_start + timedelta(days=31 * profile.commission_months)
    else:
        db.add(StoreReferralDB(
            store_id=store_id,
            referrer_user_id=req.referrer_user_id,
            source="manual",
            assigned_by_user_id=user.id,
            window_start=window_start,
            window_end=window_start + timedelta(days=31 * profile.commission_months),
        ))
    await db.commit()
    return {"ok": True}


@router.delete("/stores/{store_id}/referrer")
async def remove_referrer(store_id: str, db: AsyncSession = Depends(get_db)):
    existing = await db.get(StoreReferralDB, store_id)
    if existing:
        await db.delete(existing)
        await db.commit()
    return {"ok": True}


@router.post("/stores/{store_id}/reevaluate")
async def reevaluate_store(store_id: str, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    status_ = await asyncio.to_thread(billing_engine.evaluate_block, store_id)
    return {"ok": True, "billing_status": status_}


@router.post("/stores/{store_id}/charge-now")
async def charge_store_now(store_id: str, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    charged = await asyncio.to_thread(billing_engine.charge_period, store_id)
    status_ = await asyncio.to_thread(billing_engine.evaluate_block, store_id)
    return {"ok": True, "periods_charged": charged, "billing_status": status_}


@router.get("/transactions")
async def list_all_transactions(store_id: Optional[str] = None, limit: int = 100, offset: int = 0):
    if not store_id:
        return []
    return await asyncio.to_thread(billing_ledger.list_transactions, store_id, limit, offset)


@router.get("/referrers")
async def list_referrers(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ReferralProfileDB, User.email).join(User, User.id == ReferralProfileDB.user_id))
    rows = res.all()
    return [
        {
            "user_id": profile.user_id, "email": email, "code": profile.code,
            "commission_percent": profile.commission_percent, "commission_months": profile.commission_months,
            "is_active": profile.is_active,
        }
        for profile, email in rows
    ]


@router.get("/users/lookup")
async def lookup_user_by_email(email: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь с таким email не найден")
    return {"user_id": user.id, "email": user.email}


@router.post("/referrers")
async def create_referrer(req: ReferrerCreateRequest, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    existing = await db.get(ReferralProfileDB, req.user_id)
    if existing:
        raise HTTPException(status_code=400, detail="У пользователя уже есть реферальный профиль")
    code = await asyncio.to_thread(_generate_code_sync)
    db.add(ReferralProfileDB(
        user_id=req.user_id, code=code,
        commission_percent=req.commission_percent, commission_months=req.commission_months,
        is_active=True,
    ))
    await db.commit()
    return {"ok": True, "code": code}


def _generate_code_sync() -> str:
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return str(uuid.uuid4())[:8].upper()
    try:
        return billing_referrals.generate_code(session)
    finally:
        session.close()


@router.get("/referrers/{referrer_user_id}/accruals")
async def get_referrer_accruals(referrer_user_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(ReferralAccrualDB).where(ReferralAccrualDB.referrer_user_id == referrer_user_id)
        .order_by(ReferralAccrualDB.created_at.desc())
    )
    rows = res.scalars().all()
    return [
        {
            "id": r.id, "store_id": r.store_id, "amount_minor": r.amount_minor,
            "percent": r.percent, "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        }
        for r in rows
    ]


@router.post("/accruals/{accrual_id}/mark-paid")
async def mark_accrual_paid(accrual_id: str, payout_ref: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    accrual = await db.get(ReferralAccrualDB, accrual_id)
    if not accrual:
        raise HTTPException(status_code=404, detail="Начисление не найдено")
    accrual.status = "paid"
    accrual.paid_at = datetime.utcnow()
    accrual.payout_ref = payout_ref
    await db.commit()
    return {"ok": True}
