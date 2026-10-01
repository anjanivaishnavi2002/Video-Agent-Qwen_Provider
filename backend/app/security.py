"""Access control. Candidates hold a per-interview secret; recruiters use an admin key."""
import hmac

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Interview


def _same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def _check_token(db: Session, session_id: int, token: str | None) -> bool:
    interview = db.get(Interview, session_id)
    return bool(interview) and _same(interview.access_token, token)


def require_session_token(
    session_id: int,
    x_session_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> None:
    """The browser that started the interview received a secret token; nothing else works."""
    if not _check_token(db, session_id, x_session_token):
        raise HTTPException(status_code=403, detail="Invalid or missing session token")


def require_admin_or_session(
    session_id: int,
    x_api_key: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> None:
    if settings.ADMIN_API_KEY and _same(settings.ADMIN_API_KEY, x_api_key):
        return
    if _check_token(db, session_id, x_session_token):
        return
    raise HTTPException(status_code=403, detail="Not allowed")
