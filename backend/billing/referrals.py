"""Реферальные начисления — 50% от РЕАЛЬНОГО пополнения приведённого
магазина, первые 3 месяца (решение владельца, 2026-08-12; НЕ при списании за
тариф). Три ограничителя работают вместе (каждый по отдельности дырявый):
материализованное window_end, счётчик уникальных периодов < commission_months,
и потолок — суммарные начисления по магазину не превышают
commission_percent% от суммы, реально списанной с него за тариф в пределах
окна (иначе разовая крупная предоплата выплатила бы рефереру половину
предоплаты, а не половину фактического потребления — инженерное решение
поверх ответа владельца, ПОДТВЕРЖДЕНО владельцем 2026-08-12: если потолок
где-то ошибётся не в пользу реферера, суперадмин может вручную докорректировать
конкретное начисление через /api/admin/stores/{id}/adjust, отдельного
механизма не нужно).
"""
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


def accrue_on_topup(store_id: str, source_transaction_id: str, amount_minor: int) -> Optional[dict]:
    """Вызывается после успешного пополнения (topup_manual/topup_gateway).
    Идемпотентно: source_transaction_id UNIQUE на referral_accruals — повтор
    с тем же txn_id не создаст вторую запись."""
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import StoreReferralDB, ReferralProfileDB, ReferralAccrualDB, BillingTransactionDB
    from sqlalchemy import select, func
    import datetime as _dt

    session = _get_sync_db_session()
    if not session:
        return None
    try:
        ref = session.get(StoreReferralDB, store_id)
        if not ref:
            return None
        profile = session.get(ReferralProfileDB, ref.referrer_user_id)
        if not profile or not profile.is_active:
            return None

        now = _dt.datetime.utcnow()
        if not (ref.window_start <= now < ref.window_end):
            return None

        periods_paid = session.execute(
            select(func.count(func.distinct(ReferralAccrualDB.period_start)))
            .where(ReferralAccrualDB.store_id == store_id, ReferralAccrualDB.referrer_user_id == ref.referrer_user_id)
        ).scalar() or 0
        if periods_paid >= profile.commission_months:
            return None

        amount = abs(amount_minor) * profile.commission_percent // 100

        # Потолок: не начислять больше, чем commission_percent% от суммы,
        # реально списанной с этого магазина за тариф в пределах окна.
        charged_total = session.execute(
            select(func.coalesce(func.sum(func.abs(BillingTransactionDB.amount_minor)), 0))
            .where(
                BillingTransactionDB.store_id == store_id,
                BillingTransactionDB.kind.in_(["charge_subscription", "charge_credit_addon", "charge_prompt_addon"]),
                BillingTransactionDB.created_at >= ref.window_start,
                BillingTransactionDB.created_at < ref.window_end,
            )
        ).scalar() or 0
        cap = charged_total * profile.commission_percent // 100
        already_accrued = session.execute(
            select(func.coalesce(func.sum(ReferralAccrualDB.amount_minor), 0))
            .where(ReferralAccrualDB.store_id == store_id, ReferralAccrualDB.referrer_user_id == ref.referrer_user_id)
        ).scalar() or 0
        amount = max(0, min(amount, cap - already_accrued))
        if amount <= 0:
            return None

        accrual = ReferralAccrualDB(
            id=str(uuid.uuid4()),
            referrer_user_id=ref.referrer_user_id,
            store_id=store_id,
            source_transaction_id=source_transaction_id,
            amount_minor=amount,
            percent=profile.commission_percent,
            period_start=now,
            period_end=now,
            status="accrued",
        )
        session.add(accrual)
        session.commit()
        return {"referrer_user_id": ref.referrer_user_id, "amount_minor": amount}
    except Exception as e:
        logger.error(f"[billing.referrals] accrue_on_topup failed for {store_id}: {e}")
        session.rollback()
        return None
    finally:
        session.close()


def generate_code(session, length: int = 8) -> str:
    import random
    import string
    from sqlalchemy import select
    from backend.db.models import ReferralProfileDB
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = "".join(random.choice(alphabet) for _ in range(length))
        row = session.execute(select(ReferralProfileDB).where(ReferralProfileDB.code == code)).first()
        if not row:
            return code
    raise RuntimeError("Не удалось сгенерировать уникальный реферальный код")
