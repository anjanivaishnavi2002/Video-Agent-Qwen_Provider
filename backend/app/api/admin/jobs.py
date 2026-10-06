from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.admin.common import like_pattern, paginate
from app.api.resume import clean_skills
from app.db.database import get_db
from app.db.models import AdminUser, Candidate, Job
from app.security import require_admin, require_superadmin

router = APIRouter(prefix="/jobs", tags=["admin: jobs"])

JOB_STATUSES = {"draft", "open", "closed"}


class JobIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=10, max_length=20000)
    department: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    required_skills: list[str] | None = None
    status: str = "open"


class JobPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, min_length=10, max_length=20000)
    department: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    required_skills: list[str] | None = None
    status: str | None = None


def job_out(job: Job, candidate_count: int | None = None) -> dict:
    out = {"id": job.id, "title": job.title, "department": job.department, "location": job.location,
           "employment_type": job.employment_type, "description": job.description,
           "required_skills": job.required_skills or [], "status": job.status,
           "created_at": job.created_at, "updated_at": job.updated_at}
    if candidate_count is not None:
        out["candidate_count"] = candidate_count
    return out


def _check_status(value: str | None) -> None:
    if value is not None and value not in JOB_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(JOB_STATUSES)}")


@router.get("")
def list_jobs(q: str | None = None, status: str | None = None, page: int = Query(1, ge=1),
              page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
              _: AdminUser = Depends(require_admin)):
    query = db.query(Job).order_by(Job.created_at.desc())
    if status:
        query = query.filter(Job.status == status)
    if q:
        query = query.filter(Job.title.ilike(like_pattern(q), escape="\\"))
    items, total = paginate(query, page, page_size)
    counts = dict(db.query(Candidate.job_id, func.count(Candidate.id))
                  .group_by(Candidate.job_id).all())
    return {"items": [job_out(j, counts.get(j.id, 0)) for j in items], "total": total, "page": page,
            "page_size": page_size}


@router.post("", status_code=201)
def create_job(data: JobIn, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    _check_status(data.status)
    job = Job(title=data.title.strip(), description=data.description.strip(), department=data.department,
              location=data.location, employment_type=data.employment_type,
              required_skills=clean_skills(data.required_skills), status=data.status, created_by=admin.id)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job_out(job, 0)


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_out(job, db.query(Candidate).filter(Candidate.job_id == job_id).count())


@router.patch("/{job_id}")
def update_job(job_id: int, data: JobPatch, db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    changes = data.model_dump(exclude_unset=True)
    _check_status(changes.get("status"))
    if "required_skills" in changes:
        changes["required_skills"] = clean_skills(changes["required_skills"])
    for field, value in changes.items():
        if field in ("title", "description") and value is None:
            continue
        setattr(job, field, value)
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job_out(job)


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(require_superadmin)):
    """Jobs that candidates applied to are closed, not deleted (their applications keep the reference)."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if db.query(Candidate).filter(Candidate.job_id == job_id).count():
        job.status = "closed"
        db.commit()
        return {"status": "closed", "detail": "Job has applications, so it was closed instead of deleted."}
    db.delete(job)
    db.commit()
    return {"status": "deleted"}
