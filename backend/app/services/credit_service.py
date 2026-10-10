"""
Credits: the company buys/receives credits; unlocking one candidate's interview (recording + full detail) costs
UNLOCK_CREDIT_COST. Everything is recorded in an append-only ledger. The wallet row is locked while credits move
(SELECT ... FOR UPDATE on PostgreSQL) so two admins can never spend the same credits, and `unlock_key` is unique
so unlocking the same interview twice never charges twice.
"""
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AdminUser, CreditLedger, CreditWallet, Interview


def _wallet(db: Session, lock: bool = False) -> CreditWallet:
    query = db.query(CreditWallet).filter(CreditWallet.id == 1)
    wallet = (query.with_for_update() if lock else query).first()
    if wallet is None:
        wallet = CreditWallet(id=1, balance=0)
        db.add(wallet)
        try:
            db.flush()
        except IntegrityError:          # another request created it first
            db.rollback()
            wallet = db.query(CreditWallet).filter(CreditWallet.id == 1).first()
        if lock:
            wallet = db.query(CreditWallet).filter(CreditWallet.id == 1).with_for_update().first()
    return wallet


def balance(db: Session) -> int:
    return _wallet(db).balance


def is_unlocked(interview: Interview) -> bool:
    return bool(interview.video_unlocked_at) or not settings.REQUIRE_UNLOCK


def grant(db: Session, admin: AdminUser, amount: int, note: str | None) -> int:
    if amount == 0 or abs(amount) > 1_000_000:
        raise HTTPException(status_code=400, detail="Enter a non-zero amount (up to 1,000,000).")
    wallet = _wallet(db, lock=True)
    if wallet.balance + amount < 0:
        raise HTTPException(status_code=400, detail="That would make the balance negative.")
    wallet.balance += amount
    db.add(CreditLedger(delta=amount, reason="grant" if amount > 0 else "adjustment", note=(note or "")[:300] or None,
                        admin_id=admin.id, balance_after=wallet.balance))
    db.commit()
    return wallet.balance


def unlock_interview(db: Session, admin: AdminUser, interview: Interview) -> dict:
    """Idempotent: an already unlocked interview is returned without charging."""
    if interview.video_unlocked_at:
        return {"unlocked": True, "charged": 0, "balance": balance(db)}
    cost = max(0, settings.UNLOCK_CREDIT_COST)
    wallet = _wallet(db, lock=True)
    db.refresh(interview)
    if interview.video_unlocked_at:                 # unlocked by someone else while we waited for the lock
        db.rollback()
        return {"unlocked": True, "charged": 0, "balance": balance(db)}
    if wallet.balance < cost:
        db.rollback()
        raise HTTPException(status_code=402, detail=f"Not enough credits: unlocking costs {cost}, "
                                                    f"you have {wallet.balance}.")
    wallet.balance -= cost
    db.add(CreditLedger(delta=-cost, reason="unlock", interview_id=interview.id, candidate_id=interview.candidate_id,
                        admin_id=admin.id, balance_after=wallet.balance, unlock_key=f"unlock:{interview.id}"))
    interview.video_unlocked_at = datetime.utcnow()
    interview.video_unlocked_by = admin.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"unlocked": True, "charged": 0, "balance": balance(db)}
    return {"unlocked": True, "charged": cost, "balance": wallet.balance}
