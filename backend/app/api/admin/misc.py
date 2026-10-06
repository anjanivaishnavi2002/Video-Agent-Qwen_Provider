"""Notifications log, dashboard statistics, admin-user management, system check."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.admin.auth import admin_public
from app.api.admin.candidates import notification_out
from app.api.admin.common import paginate
from app.config import settings
from app.db.database import get_db
from app.db.models import AdminUser, Candidate, Evaluation, Interview, Job, NotificationRecord
from app.providers.llm_errors import LLMError
from app.security import ADMIN_ROLES, hash_password, require_admin, require_superadmin

notifications_router = APIRouter(prefix="/notifications", tags=["admin: notifications"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["admin: dashboard"])
users_router = APIRouter(prefix="/users", tags=["admin: users"])
system_router = APIRouter(prefix="/system", tags=["admin: system"])


@notifications_router.get("")
def list_notifications(candidate_id: int | None = None, status: str | None = None, channel: str | None = None,
                       page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                       db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    query = db.query(NotificationRecord).order_by(NotificationRecord.created_at.desc())
    for column, value in ((NotificationRecord.candidate_id, candidate_id), (NotificationRecord.status, status),
                          (NotificationRecord.channel, channel)):
        if value:
            query = query.filter(column == value)
    items, total = paginate(query, page, page_size)
    return {"items": [notification_out(n) for n in items], "total": total, "page": page, "page_size": page_size}


@dashboard_router.get("/stats")
def stats(db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    by_status = dict(db.query(Candidate.interview_status, func.count(Candidate.id))
                     .group_by(Candidate.interview_status).all())
    since = datetime.utcnow() - timedelta(days=7)
    return {
        "candidates_total": sum(by_status.values()),
        "candidates_by_status": by_status,
        "open_jobs": db.query(Job).filter(Job.status == "open").count(),
        "interviews_running": db.query(Interview).filter(Interview.status == "running").count(),
        "interviews_last_7_days": db.query(Interview).filter(Interview.created_at >= since).count(),
        "average_score": db.query(func.avg(Evaluation.overall_score)).scalar(),
        "notifications_failed_last_7_days": db.query(NotificationRecord).filter(
            NotificationRecord.status == "failed", NotificationRecord.created_at >= since).count(),
    }


class AdminCreate(BaseModel):
    email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10, max_length=72)
    full_name: str | None = Field(default=None, max_length=200)
    role: str = "recruiter"


class AdminPatch(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=72)


@users_router.get("")
def list_admins(db: Session = Depends(get_db), _: AdminUser = Depends(require_superadmin)):
    return [{**admin_public(a), "is_active": a.is_active, "last_login_at": a.last_login_at}
            for a in db.query(AdminUser).order_by(AdminUser.id).all()]


@users_router.post("", status_code=201)
def create_admin(data: AdminCreate, db: Session = Depends(get_db), _: AdminUser = Depends(require_superadmin)):
    if data.role not in ADMIN_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {list(ADMIN_ROLES)}")
    email = data.email.strip().lower()
    if db.query(AdminUser).filter(AdminUser.email == email).first():
        raise HTTPException(status_code=409, detail="An admin with this e-mail already exists.")
    admin = AdminUser(email=email, full_name=data.full_name, role=data.role,
                      password_hash=hash_password(data.password))
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin_public(admin)


@users_router.patch("/{admin_id}")
def update_admin(admin_id: int, data: AdminPatch, db: Session = Depends(get_db),
                 me: AdminUser = Depends(require_superadmin)):
    admin = db.get(AdminUser, admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")
    changes = data.model_dump(exclude_unset=True)
    if admin.id == me.id and (changes.get("is_active") is False or changes.get("role", "admin") != "admin"):
        raise HTTPException(status_code=400, detail="You cannot deactivate or demote your own account.")
    if "role" in changes and changes["role"] not in ADMIN_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {list(ADMIN_ROLES)}")
    if "full_name" in changes:
        admin.full_name = changes["full_name"]
    if "role" in changes:
        admin.role = changes["role"]
    if "is_active" in changes:
        admin.is_active = changes["is_active"]
    if changes.get("password"):
        admin.password_hash = hash_password(changes["password"])
    admin.token_version = (admin.token_version or 0) + 1      # any change signs that admin out
    db.commit()
    return {**admin_public(admin), "is_active": admin.is_active}


@system_router.get("/llm-check")
def llm_check(_: AdminUser = Depends(require_admin)):
    """One tiny real Vertex AI call: proves project, location, model, IAM and network. Admin-only (it costs a request)."""
    from app.providers.factory import get_llm

    try:
        return {"status": "ok", "provider": settings.llm_backend, "model": settings.active_model,
                "location": settings.GOOGLE_CLOUD_LOCATION, "check": get_llm().ping()}
    except LLMError as exc:
        return {"status": exc.code, "provider": settings.llm_backend, "model": settings.active_model,
                "error": exc.message}
