"""Candidate application: details + resume upload (public endpoint; the file is validated, then stored privately)."""
import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Candidate, Job
from app.security import new_token
from app.services import storage
from app.services.consent_service import get_consent
from app.services.resume_service import content_type_for, extract_text, save_upload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/resume", tags=["Resume"])

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{6,18}[0-9]$")


def clean_skills(raw: str | list | None) -> list[str]:
    items = raw if isinstance(raw, list) else str(raw or "").split(",")
    out: list[str] = []
    for item in items:
        skill = str(item).strip()[:60]
        if skill and skill.lower() not in (s.lower() for s in out):
            out.append(skill)
    return out[:30]


def validate_candidate_fields(*, name: str, email: str, phone: str, location: str,
                              experience_years: float | None) -> dict:
    """Shared by the public form and the admin edit endpoint. Raises HTTPException(400) with a readable message."""
    name = " ".join((name or "").split())
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    location = " ".join((location or "").split())
    if not name or len(name) > 120:
        raise HTTPException(status_code=400, detail="Please enter your name (up to 120 characters).")
    if email and (len(email) > 255 or not _EMAIL.match(email)):
        raise HTTPException(status_code=400, detail="Please enter a valid e-mail address.")
    if phone and not _PHONE.match(phone):
        raise HTTPException(status_code=400, detail="Please enter a valid phone number, e.g. +91 98765 43210.")
    if len(location) > 200:
        raise HTTPException(status_code=400, detail="Location is too long.")
    if experience_years is not None and not (0 <= experience_years <= 60):
        raise HTTPException(status_code=400, detail="Experience must be between 0 and 60 years.")
    return {"name": name, "email": email or None, "phone": phone or None, "location": location or None,
            "experience_years": experience_years}


@router.post("/upload")
def upload_resume(
    name: str = Form(...),
    consent_version: str = Form(...),
    file: UploadFile = File(...),
    email: str = Form(...),
    phone: str = Form(""),
    location: str = Form(""),
    experience_years: float | None = Form(None),
    skills: str = Form(""),
    job_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    if consent_version != get_consent()["version"]:
        raise HTTPException(
            status_code=400,
            detail="Please read and accept the current consent form before continuing.",
        )

    fields = validate_candidate_fields(name=name, email=email, phone=phone, location=location,
                                       experience_years=experience_years)
    if not fields["email"]:
        raise HTTPException(status_code=400, detail="Please enter your e-mail address.")

    job = None
    if job_id is not None:
        job = db.get(Job, job_id)
        if not job or job.status != "open":
            raise HTTPException(status_code=400, detail="This job is not open for applications.")

    # Read at most limit+1 bytes so an oversized upload cannot exhaust memory.
    limit = settings.MAX_RESUME_MB * 1024 * 1024
    data = file.file.read(limit + 1)
    try:
        path = save_upload(file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        text = extract_text(path)
    except Exception:
        logger.exception("Could not parse an uploaded resume")
        Path(path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=422, detail="This file could not be read. Please upload a valid resume."
        )

    if len(text) < settings.MIN_RESUME_CHARS:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail="Could not read enough text from this resume (it may be a scanned image).",
        )

    ext = Path(path).suffix.lower()
    try:
        stored_path = storage.store_file(path, f"resumes/{Path(path).name}", content_type=content_type_for(ext))
    except Exception:
        logger.exception("Could not store resume")
        Path(path).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not store the resume. Please try again.")

    candidate = Candidate(
        **fields,
        skills=clean_skills(skills),
        job_id=job.id if job else None,
        resume_filename=Path(file.filename or "resume").name[:200],
        resume_path=stored_path,
        resume_content_type=content_type_for(ext),
        resume_size_bytes=len(data),
        resume_text=text[: settings.RESUME_STORE_MAX_CHARS],
        consent_version=consent_version,
        consented_at=datetime.utcnow(),
        interview_status="applied",
        invite_token=new_token(),
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return {"candidate_id": candidate.id, "name": candidate.name, "invite_token": candidate.invite_token}
