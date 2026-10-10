"""
Access control.

* Candidates hold a per-interview secret (X-Session-Token) and a per-candidate invite token.
* Admins / recruiters authenticate with e-mail + password and receive a short-lived JWT (Authorization: Bearer ...).
  EVERY admin API is protected here, on the backend - hiding a page in the frontend is never the protection.
"""
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import AdminUser, CandidateAccount, Interview

JWT_ISSUER = "video-agent"
JWT_AUDIENCE = "video-agent-admin"
JWT_CANDIDATE_AUDIENCE = "video-agent-candidate"
ADMIN_ROLES = ("admin", "recruiter")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def new_token() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Passwords + admin JWT
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("ascii"))
    except ValueError:
        return False


# Used to spend the same time when the e-mail is unknown (no user enumeration through timing).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(8))


def create_admin_token(admin: AdminUser) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    if len(settings.JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET is not configured (needs at least 32 characters).")
    now = datetime.now(timezone.utc)
    lifetime = timedelta(minutes=settings.ADMIN_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(admin.id), "role": admin.role, "tv": admin.token_version,
        "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "iat": now, "exp": now + lifetime,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM), int(lifetime.total_seconds())


def create_candidate_token(account: CandidateAccount) -> tuple[str, int]:
    if len(settings.JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET is not configured (needs at least 32 characters).")
    now = datetime.now(timezone.utc)
    lifetime = timedelta(minutes=settings.CANDIDATE_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(account.id), "tv": account.token_version, "iss": JWT_ISSUER,
               "aud": JWT_CANDIDATE_AUDIENCE, "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM), int(lifetime.total_seconds())


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _decode_admin_token(token: str) -> dict:
    try:
        return jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM],
            audience=JWT_AUDIENCE, issuer=JWT_ISSUER, options={"require": ["exp", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError:
        raise _unauthorized("Invalid or expired token") from None


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def get_current_admin(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AdminUser:
    """Dependency: the authenticated, active admin user - or 401."""
    token = _bearer(authorization)
    if not token or len(settings.JWT_SECRET) < 32:
        raise _unauthorized()
    claims = _decode_admin_token(token)
    try:
        admin = db.get(AdminUser, int(claims["sub"]))
    except (ValueError, TypeError):
        raise _unauthorized("Invalid token") from None
    if not admin or not admin.is_active or admin.token_version != claims.get("tv"):
        raise _unauthorized("Session expired. Please sign in again.")
    return admin


def get_current_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> CandidateAccount:
    """Dependency: the signed-in candidate account (audience differs from admin tokens, so they never mix)."""
    token = _bearer(authorization)
    if not token or len(settings.JWT_SECRET) < 32:
        raise _unauthorized()
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM],
                            audience=JWT_CANDIDATE_AUDIENCE, issuer=JWT_ISSUER,
                            options={"require": ["exp", "sub", "iss", "aud"]})
        account = db.get(CandidateAccount, int(claims["sub"]))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise _unauthorized("Invalid or expired token") from None
    if not account or not account.is_active or account.token_version != claims.get("tv"):
        raise _unauthorized("Session expired. Please sign in again.")
    return account


def require_admin(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
    """Any signed-in admin or recruiter."""
    if admin.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Not allowed")
    return admin


def require_superadmin(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
    """Role 'admin' only (managing admin accounts, deleting data)."""
    if admin.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    return admin


# ---------------------------------------------------------------------------
# Login throttling (best effort, per instance; Cloud Armor / WAF is the strong layer)
# ---------------------------------------------------------------------------

_attempts: dict[str, deque] = defaultdict(deque)
_attempts_lock = threading.Lock()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "?")) or "?"


def _throttle_key(email: str, ip: str) -> str:
    return f"{email.strip().lower()}|{ip}"


def check_login_allowed(email: str, ip: str) -> None:
    now = time.time()
    with _attempts_lock:
        bucket = _attempts[_throttle_key(email, ip)]
        while bucket and now - bucket[0] > settings.LOGIN_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= settings.LOGIN_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in a few minutes.",
                                headers={"Retry-After": str(settings.LOGIN_WINDOW_SECONDS)})


def record_login_failure(email: str, ip: str) -> None:
    with _attempts_lock:
        _attempts[_throttle_key(email, ip)].append(time.time())


def clear_login_failures(email: str, ip: str) -> None:
    with _attempts_lock:
        _attempts.pop(_throttle_key(email, ip), None)


# ---------------------------------------------------------------------------
# Candidate / interview-session access
# ---------------------------------------------------------------------------

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
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> None:
    """Read access to one interview: the candidate's own session token, an admin JWT, or (legacy) ADMIN_API_KEY."""
    if _check_token(db, session_id, x_session_token):
        return
    token = _bearer(authorization)
    if token and len(settings.JWT_SECRET) >= 32:
        try:
            claims = _decode_admin_token(token)
            admin = db.get(AdminUser, int(claims["sub"]))
            if admin and admin.is_active and admin.token_version == claims.get("tv") and admin.role in ADMIN_ROLES:
                return
        except (HTTPException, ValueError, TypeError):
            pass
    if settings.ADMIN_API_KEY and _same(settings.ADMIN_API_KEY, x_api_key):
        return
    raise HTTPException(status_code=403, detail="Not allowed")


def caller_role(
    session_id: int,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> str:
    """Who is reading this interview: "admin" (admin JWT or legacy API key) or "candidate" (its own session token).

    Admins get the full reviewer report; the candidate only gets a short feedback view (no scores, no camera or
    proctoring counts). Anything else is rejected.
    """
    token = _bearer(authorization)
    if token and len(settings.JWT_SECRET) >= 32:
        try:
            claims = _decode_admin_token(token)
            admin = db.get(AdminUser, int(claims["sub"]))
            if admin and admin.is_active and admin.token_version == claims.get("tv") and admin.role in ADMIN_ROLES:
                return "admin"
        except (HTTPException, ValueError, TypeError):
            pass
    if settings.ADMIN_API_KEY and _same(settings.ADMIN_API_KEY, x_api_key):
        return "admin"
    if _check_token(db, session_id, x_session_token):
        return "candidate"
    raise HTTPException(status_code=403, detail="Not allowed")
