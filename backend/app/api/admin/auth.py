"""Admin authentication: separate from the candidate flow. /admin/auth/login -> JWT (Bearer)."""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import AdminUser
from app.security import (
    _DUMMY_HASH, check_login_allowed, clear_login_failures, client_ip, create_admin_token, get_current_admin,
    hash_password, record_login_failure, verify_password,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["admin: authentication"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=72)   # bcrypt reads at most 72 bytes


def admin_public(admin: AdminUser) -> dict:
    return {"id": admin.id, "email": admin.email, "full_name": admin.full_name, "role": admin.role}


@router.post("/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    email, ip = data.email.strip().lower(), client_ip(request)
    check_login_allowed(email, ip)

    admin = db.query(AdminUser).filter(AdminUser.email == email).first()
    ok = verify_password(data.password, admin.password_hash if admin else _DUMMY_HASH)
    if not admin or not ok or not admin.is_active:
        record_login_failure(email, ip)
        logger.warning("Failed admin sign-in from %s", ip)       # no e-mail / password in the log
        raise HTTPException(status_code=401, detail="Incorrect e-mail or password.")

    clear_login_failures(email, ip)
    admin.last_login_at = datetime.utcnow()
    db.commit()
    token, expires_in = create_admin_token(admin)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in, "admin": admin_public(admin)}


@router.post("/logout")
def logout(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Server-side logout: invalidates every token issued to this admin (token_version is part of each JWT)."""
    admin.token_version = (admin.token_version or 0) + 1
    db.commit()
    return {"status": "signed_out"}


@router.get("/me")
def me(admin: AdminUser = Depends(get_current_admin)):
    return admin_public(admin)


@router.post("/change-password")
def change_password(data: ChangePasswordIn, admin: AdminUser = Depends(get_current_admin),
                    db: Session = Depends(get_db)):
    if not verify_password(data.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="The current password is incorrect.")
    admin.password_hash = hash_password(data.new_password)
    admin.token_version = (admin.token_version or 0) + 1      # signs out every session
    db.commit()
    return {"status": "password_changed"}
