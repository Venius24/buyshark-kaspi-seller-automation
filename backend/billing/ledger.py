"""Append-only денежный леджер — единственный источник правды по деньгам.
Все суммы в тиын (1/100 ₸), BigInteger, никогда float. Идемпотентность через
UNIQUE(idempotency_key) на billing_transactions, а не read-check-write:
вставка леджер-строки и движение баланса — одна транзакция, один commit().
Баланс может уходить в минус — гварды "не уходить в минус" нет намеренно.
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


@dataclass
class LedgerResult:
    applied: bool
    balance_minor: Optional[int] = None
    transaction_id: Optional[str] = None
    reason: Optional[str] = None


def _ensure_balance_row(session, store_id: str) -> None:
    session.execute(text(
        "INSERT INTO store_balances (store_id, balance_minor, updated_at) "
        "SELECT :store_id, 0, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM store_balances WHERE store_id = :store_id)"
    ), {"store_id": store_id})


def _move(session, store_id: str, store_amount_minor: int, kind: str, idempotency_key: Optional[str],
          **meta) -> LedgerResult:
    """store_amount_minor — знаковое движение БАЛАНСА (+ пополнение, - списание).
    Строка леджера (billing_transactions.amount_minor) хранит то же знаковое
    значение — так выписка читаема без доп. логики восстановления знака."""
    from backend.db.models import BillingTransactionDB

    key = idempotency_key or str(uuid.uuid4())
    txn_id = str(uuid.uuid4())
    try:
        _ensure_balance_row(session, store_id)
        session.add(BillingTransactionDB(
            id=txn_id,
            store_id=store_id,
            amount_minor=store_amount_minor,
            kind=kind,
            idempotency_key=key,
            status=meta.get("status", "completed"),
            provider=meta.get("provider"),
            provider_payment_id=meta.get("provider_payment_id"),
            method=meta.get("method"),
            period_start=meta.get("period_start"),
            period_end=meta.get("period_end"),
            created_by_user_id=meta.get("created_by_user_id"),
            comment=meta.get("comment"),
            raw_payload=meta.get("raw_payload", "{}"),
        ))
        session.flush()
    except IntegrityError:
        session.rollback()
        return LedgerResult(applied=False, reason="already_applied")

    session.execute(text(
        "UPDATE store_balances SET balance_minor = balance_minor + :n, updated_at = CURRENT_TIMESTAMP "
        "WHERE store_id = :store_id"
    ), {"store_id": store_id, "n": store_amount_minor})

    row = session.execute(text(
        "SELECT balance_minor FROM store_balances WHERE store_id = :store_id"
    ), {"store_id": store_id}).first()
    balance_after = row[0] if row else None

    session.execute(text(
        "UPDATE billing_transactions SET balance_after_minor = :b WHERE id = :id"
    ), {"b": balance_after, "id": txn_id})

    session.commit()
    return LedgerResult(applied=True, balance_minor=balance_after, transaction_id=txn_id)


def debit(store_id: str, amount_minor: int, kind: str, idempotency_key: Optional[str] = None, **meta) -> LedgerResult:
    """Списание. amount_minor передаётся положительным (стоимость), баланс
    двигается на -amount_minor. Может увести баланс в минус — это ожидаемо."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return LedgerResult(applied=False, reason="no_db_session")
    try:
        return _move(session, store_id, -abs(amount_minor), kind, idempotency_key, **meta)
    finally:
        session.close()


def credit(store_id: str, amount_minor: int, kind: str, idempotency_key: Optional[str] = None, **meta) -> LedgerResult:
    """Пополнение/возврат. amount_minor передаётся положительным, баланс
    двигается на +amount_minor. Для kind, начинающихся с 'topup_' (реальное
    пополнение, не technical adjustment/refund), после применения пытается
    начислить реферальную комиссию — решение владельца: комиссия считается
    от РЕАЛЬНОГО пополнения, не от списания за тариф (см. referrals.py)."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return LedgerResult(applied=False, reason="no_db_session")
    try:
        result = _move(session, store_id, abs(amount_minor), kind, idempotency_key, **meta)
        if result.applied and kind.startswith("topup_"):
            try:
                from backend.billing import referrals
                referrals.accrue_on_topup(store_id, result.transaction_id, abs(amount_minor))
            except Exception as e:
                logger.error(f"[billing.ledger] referral accrual failed for {store_id}: {e}")
        return result
    finally:
        session.close()


def balance(store_id: str) -> int:
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return 0
    try:
        row = session.execute(text(
            "SELECT balance_minor FROM store_balances WHERE store_id = :store_id"
        ), {"store_id": store_id}).first()
        return row[0] if row else 0
    finally:
        session.close()


def list_transactions(store_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import BillingTransactionDB
    session = _get_sync_db_session()
    if not session:
        return []
    try:
        rows = session.execute(
            select(BillingTransactionDB)
            .where(BillingTransactionDB.store_id == store_id)
            .order_by(BillingTransactionDB.created_at.desc())
            .limit(limit).offset(offset)
        ).scalars().all()
        return [
            {
                "id": r.id, "amount_minor": r.amount_minor, "balance_after_minor": r.balance_after_minor,
                "kind": r.kind, "status": r.status, "comment": r.comment,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()
