from datetime import datetime

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.admin.common import file_response, like_pattern, paginate
from app.api.resume import clean_skills
from app.db.database import get_db
from app.db.models import AdminUser, Candidate, Job
from app.config import settings
from app.security import require_admin, require_superadmin
from app.services import sample_jobs, storage
from app.services.resume_service import content_type_for, extract_text, save_upload

router = APIRouter(prefix="/jobs", tags=["admin: jobs"])

JOB_STATUSES = {"draft", "open", "closed"}


class JobIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=10, max_length=20000)
    department: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    required_skills: list[str] | None = None
    process_type: str | None = Field(default=None, pattern="^(voice|chat|email|blended|back_office|other)$")
    experience_min: int | None = Field(default=None, ge=0, le=50)
    experience_max: int | None = Field(default=None, ge=0, le=50)
    status: str = "open"


class JobPatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, min_length=10, max_length=20000)
    department: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    required_skills: list[str] | None = None
    process_type: str | None = Field(default=None, pattern="^(voice|chat|email|blended|back_office|other)$")
    experience_min: int | None = Field(default=None, ge=0, le=50)
    experience_max: int | None = Field(default=None, ge=0, le=50)
    status: str | None = None


def job_out(job: Job, candidate_count: int | None = None) -> dict:
    out = {"id": job.id, "title": job.title, "department": job.department, "location": job.location,
           "employment_type": job.employment_type, "description": job.description,
           "required_skills": job.required_skills or [], "status": job.status,
           "process_type": job.process_type, "experience_min": job.experience_min,
           "experience_max": job.experience_max,
           "jd_file_name": job.jd_file_name, "has_jd_file": bool(job.jd_file_path),
           "created_at": job.created_at, "updated_at": job.updated_at}
    if candidate_count is not None:
        out["candidate_count"] = candidate_count
    return out


def _check_experience(low: int | None, high: int | None) -> None:
    if low is not None and high is not None and high < low:
        raise HTTPException(status_code=400, detail="Maximum experience cannot be lower than the minimum.")


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


def _read_jd(file: UploadFile) -> tuple[str, str, bytes]:
    """Validate the JD document (PDF / DOCX / TXT, real signature) and save it temporarily. Returns (path, text, data)."""
    limit = settings.MAX_RESUME_MB * 1024 * 1024
    data = file.file.read(limit + 1)
    try:
        path = save_upload(file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        text = extract_text(path)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="This file could not be read.") from None
    if len(text.strip()) < 20:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Could not read any text from this document.")
    return path, text, data


@router.post("/sample")
def add_samples(db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    """One click: add the sample BPO jobs that are not there yet."""
    added = sample_jobs.add_sample_jobs(db)
    return {"added": added, "existing": len(sample_jobs.SAMPLES) - added}


@router.post("/extract-jd")
def extract_jd(file: UploadFile = File(...), _: AdminUser = Depends(require_admin)):
    """Read a JD document and return its text so the form can be pre-filled. Nothing is stored."""
    path, text, _data = _read_jd(file)
    Path(path).unlink(missing_ok=True)
    return {"text": text.strip()[:20000], "filename": Path(file.filename or "jd").name[:200]}


@router.post("/{job_id}/jd")
def upload_jd(job_id: int, fill_description: bool = False, file: UploadFile = File(...),
              db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    """Attach the original JD document to a job (private storage; only the reference is saved in the database)."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    path, text, data = _read_jd(file)
    ext = Path(path).suffix.lower()
    try:
        stored = storage.store_file(path, f"jd/job_{job_id}_{Path(path).name}", content_type=content_type_for(ext))
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not store the document.") from None
    old = job.jd_file_path
    job.jd_file_path, job.jd_file_name = stored, Path(file.filename or "jd").name[:200]
    job.jd_content_type, job.jd_size_bytes = content_type_for(ext), len(data)
    if fill_description:
        job.description = text.strip()[:20000]
    job.updated_at = datetime.utcnow()
    db.commit()
    if old and old != stored:
        storage.delete_file(old)
    return job_out(job)


@router.get("/{job_id}/jd")
def download_jd(job_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    job = db.get(Job, job_id)
    if not job or not job.jd_file_path:
        raise HTTPException(status_code=404, detail="No JD document for this job")
    return file_response(job.jd_file_path, filename=job.jd_file_name or "jd", media_type=job.jd_content_type,
                         label="JD document")


@router.post("", status_code=201)
def create_job(data: JobIn, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    _check_status(data.status)
    _check_experience(data.experience_min, data.experience_max)
    job = Job(title=data.title.strip(), description=data.description.strip(), department=data.department,
              location=data.location, employment_type=data.employment_type, process_type=data.process_type,
              experience_min=data.experience_min, experience_max=data.experience_max,
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
    _check_experience(changes.get("experience_min", job.experience_min), changes.get("experience_max", job.experience_max))
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
