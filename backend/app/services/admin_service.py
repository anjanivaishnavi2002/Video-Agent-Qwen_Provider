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


def deactivate_admin(db: Session, email: str) -> AdminUser:
    """Disable an admin login (kept in the table so credit/audit history stays intact). Refuses to remove the last
    active admin so nobody can lock themselves out. Bumping token_version signs out any session already issued."""
    admin = db.query(AdminUser).filter(AdminUser.email == email.strip().lower()).first()
    if not admin:
        raise ValueError("no admin with this e-mail")
    if admin.is_active and admin.role == "admin":
        others = db.query(AdminUser).filter(AdminUser.is_active.is_(True), AdminUser.role == "admin",
                                            AdminUser.id != admin.id).count()
        if not others:
            raise ValueError("this is the only active admin; create another admin first")
    admin.is_active = False
    admin.token_version = (admin.token_version or 0) + 1
    db.commit()
    db.refresh(admin)
    return admin


def reset_admin_password(db: Session, email: str, password: str) -> AdminUser:
    admin = db.query(AdminUser).filter(AdminUser.email == email.strip().lower()).first()
    if not admin:
        raise ValueError("no admin with this e-mail")
    if len(password) < 10 or len(password.encode("utf-8")) > 72:
        raise ValueError("password must be 10-72 characters")
    admin.password_hash = hash_password(password)
    admin.is_active = True
    admin.token_version = (admin.token_version or 0) + 1
    db.commit()
    db.refresh(admin)
    return admin
