import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AdminUser
from app.security import ADMIN_ROLES, hash_password

logger = logging.getLogger(__name__)


def create_admin(db: Session, email: str, password: str, *, full_name: str | None = None,
                 role: str = "admin") -> AdminUser:
    email = email.strip().lower()
    if role not in ADMIN_ROLES:
        raise ValueError(f"role must be one of {ADMIN_ROLES}")
    if len(password) < 10 or len(password.encode("utf-8")) > 72:
        raise ValueError("password must be 10-72 characters")
    if db.query(AdminUser).filter(AdminUser.email == email).first():
        raise ValueError("an admin with this e-mail already exists")
    admin = AdminUser(email=email, full_name=full_name, role=role, password_hash=hash_password(password))
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def ensure_bootstrap_admin(db: Session) -> bool:
    """First-run convenience: create the initial admin from env vars ONLY when no admin exists at all."""
    if not (settings.ADMIN_BOOTSTRAP_EMAIL and settings.ADMIN_BOOTSTRAP_PASSWORD):
        return False
    if db.query(AdminUser).count():
        return False
    create_admin(db, settings.ADMIN_BOOTSTRAP_EMAIL, settings.ADMIN_BOOTSTRAP_PASSWORD, role="admin",
                 full_name="Administrator")
    logger.info("Created the initial admin account from ADMIN_BOOTSTRAP_* (remove these variables now).")
    return True
