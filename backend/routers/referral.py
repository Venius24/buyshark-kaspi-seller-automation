"""API реферера (Слайс 7) — видит только свои магазины и начисления.
referrer_user_id всегда берётся из require_referrer (профиль по текущему
JWT), никогда не принимается из запроса."""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.db.database import get_db
from backend.db.models import ReferralProfileDB, StoreReferralDB, ReferralAccrualDB, Store
from backend.security.platform_roles import require_referrer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/referral", tags=["Referral"])


@router.get("/me")
async def get_referral_profile(profile: ReferralProfileDB = Depends(require_referrer), db: AsyncSession = Depends(get_db)):
    total_res = await db.execute(
        select(func.coalesce(func.sum(ReferralAccrualDB.amount_minor), 0))
        .where(ReferralAccrualDB.referrer_user_id == profile.user_id)
    )
    total_earned_minor = total_res.scalar() or 0
    stores_res = await db.execute(select(func.count()).where(StoreReferralDB.referrer_user_id == profile.user_id))
    stores_count = stores_res.scalar() or 0
    return {
        "code": profile.code,
        "commission_percent": profile.commission_percent,
        "commission_months": profile.commission_months,
        "total_earned_minor": total_earned_minor,
        "stores_count": stores_count,
    }


@router.get("/stores")
async def get_referral_stores(profile: ReferralProfileDB = Depends(require_referrer), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(StoreReferralDB, Store.name).join(Store, Store.id == StoreReferralDB.store_id)
        .where(StoreReferralDB.referrer_user_id == profile.user_id)
    )
    rows = res.all()
    now = datetime.utcnow()
    out = []
    for ref, store_name in rows:
        periods_res = await db.execute(
            select(func.count(func.distinct(ReferralAccrualDB.period_start)))
            .where(ReferralAccrualDB.store_id == ref.store_id, ReferralAccrualDB.referrer_user_id == profile.user_id)
        )
        months_paid = periods_res.scalar() or 0
        earned_res = await db.execute(
            select(func.coalesce(func.sum(ReferralAccrualDB.amount_minor), 0))
            .where(ReferralAccrualDB.store_id == ref.store_id, ReferralAccrualDB.referrer_user_id == profile.user_id)
        )
        earned_minor = earned_res.scalar() or 0
        out.append({
            "store_id": ref.store_id,
            "store_name": store_name,
            "window_start": ref.window_start.isoformat(),
            "window_end": ref.window_end.isoformat(),
            "window_active": ref.window_start <= now < ref.window_end,
            "months_paid": months_paid,
            "months_total": profile.commission_months,
            "earned_minor": earned_minor,
        })
    return out


@router.get("/accruals")
async def get_referral_accruals(profile: ReferralProfileDB = Depends(require_referrer), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(ReferralAccrualDB).where(ReferralAccrualDB.referrer_user_id == profile.user_id)
        .order_by(ReferralAccrualDB.created_at.desc())
    )
    rows = res.scalars().all()
    return [
        {
            "id": r.id, "store_id": r.store_id, "amount_minor": r.amount_minor,
            "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
