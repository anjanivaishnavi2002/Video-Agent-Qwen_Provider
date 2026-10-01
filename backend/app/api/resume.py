import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Candidate
from app.services import storage
from app.services.consent_service import get_consent
from app.services.resume_service import extract_text, save_upload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/resume", tags=["Resume"])


@router.post("/upload")
def upload_resume(
    name: str = Form(...),
    consent_version: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if consent_version != get_consent()["version"]:
        raise HTTPException(
            status_code=400,
            detail="Please read and accept the current consent form before continuing.",
        )

    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Please enter your name.")

    data = file.file.read()
    try:
        path = save_upload(file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        text = extract_text(path)
    except Exception:
        logger.exception("Could not parse resume %s", file.filename)
        raise HTTPException(
            status_code=422, detail="This file could not be read. Please upload a valid resume."
        )

    if len(text) < settings.MIN_RESUME_CHARS:
        raise HTTPException(
            status_code=422,
            detail="Could not read enough text from this resume (it may be a scanned image).",
        )

    try:
        stored_path = storage.store_file(path, f"resumes/{Path(path).name}")
    except Exception:
        logger.exception("Could not store resume")
        raise HTTPException(status_code=500, detail="Could not store the resume. Please try again.")

    candidate = Candidate(
        name=name, resume_filename=file.filename, resume_path=stored_path, resume_text=text,
        consent_version=consent_version, consented_at=datetime.utcnow(),
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return {"candidate_id": candidate.id, "name": candidate.name}
