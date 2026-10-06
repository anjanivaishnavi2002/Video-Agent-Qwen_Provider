import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.admin.common import file_response, like_pattern, paginate
from app.api.resume import clean_skills, validate_candidate_fields
from app.db.database import get_db
from app.db.models import CANDIDATE_STATUSES, AdminUser, Candidate, Evaluation, Interview, Job, NotificationRecord
from app.security import new_token, require_admin, require_superadmin
from app.services import candidate_service, notification_client, storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/candidates", tags=["admin: candidates"])

SORTS = {"newest": Candidate.created_at.desc(), "oldest": Candidate.created_at.asc(),
         "score": Candidate.interview_score.desc().nullslast(), "name": Candidate.name.asc()}


class CandidatePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    location: str | None = Field(default=None, max_length=200)
    experience_years: float | None = None
    skills: list[str] | None = None
    job_id: int | None = None
    interview_status: str | None = None
    reset_attempts: bool = False        # give the candidate a fresh set of interview attempts


class NotifyIn(BaseModel):
    kind: str = "reminder"              # invitation | reminder | completion | status_update | custom
    channels: list[str] | None = None   # email | sms | call. Default: e-mail and SMS where details exist
    message: str | None = Field(default=None, max_length=1000)   # required for kind="custom"
    subject: str | None = Field(default=None, max_length=200)    # optional e-mail subject for kind="custom"


def candidate_row(c: Candidate) -> dict:
    return {
        "id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "location": c.location,
        "experience_years": c.experience_years, "skills": c.skills or [],
        "job_id": c.job_id, "job_title": c.job.title if c.job else None,
        "interview_status": c.interview_status, "interview_attempts": c.interview_attempts,
        "interview_score": c.interview_score, "resume_filename": c.resume_filename,
        "has_resume": bool(c.resume_path), "created_at": c.created_at, "updated_at": c.updated_at,
    }


def evaluation_out(e: Evaluation | None) -> dict | None:
    if not e:
        return None
    return {"id": e.id, "interview_id": e.interview_id, "overall_score": e.overall_score,
            "recommendation": e.recommendation, "summary": e.summary, "strengths": e.strengths or [],
            "concerns": e.concerns or [], "criteria": e.criteria or [], "model": e.model, "created_at": e.created_at}


def interview_row(i: Interview) -> dict:
    return {"id": i.id, "candidate_id": i.candidate_id, "attempt_number": i.attempt_number, "status": i.status,
            "end_reason": i.end_reason, "started_at": i.started_at, "ended_at": i.ended_at,
            "has_recording": bool(i.video_path), "overall_score": i.evaluation.overall_score if i.evaluation else None,
            "recommendation": i.evaluation.recommendation if i.evaluation else None}


def notification_out(n: NotificationRecord) -> dict:
    return {"id": n.id, "candidate_id": n.candidate_id, "interview_id": n.interview_id, "channel": n.channel,
            "kind": n.kind, "recipient": n.recipient, "status": n.status, "provider": n.provider,
            "error": n.error, "created_at": n.created_at, "sent_at": n.sent_at}


def _get(db: Session, candidate_id: int) -> Candidate:
    candidate = db.get(Candidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


@router.get("")
def list_candidates(
    q: str | None = Query(None, max_length=100), status: str | None = None, job_id: int | None = None,
    min_score: float | None = Query(None, ge=0, le=100), sort: str = "newest",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db), _: AdminUser = Depends(require_admin),
):
    query = db.query(Candidate)
    if q:
        pattern = like_pattern(q)
        query = query.filter(or_(*(col.ilike(pattern, escape="\\") for col in
                                   (Candidate.name, Candidate.email, Candidate.phone, Candidate.location))))
    if status:
        query = query.filter(Candidate.interview_status == status)
    if job_id:
        query = query.filter(Candidate.job_id == job_id)
    if min_score is not None:
        query = query.filter(Candidate.interview_score >= min_score)
    query = query.order_by(SORTS.get(sort, SORTS["newest"]), Candidate.id.desc())
    items, total = paginate(query, page, page_size)
    return {"items": [candidate_row(c) for c in items], "total": total, "page": page, "page_size": page_size}


@router.get("/{candidate_id}")
def candidate_detail(candidate_id: int, include_resume_text: bool = False, db: Session = Depends(get_db),
                     _: AdminUser = Depends(require_admin)):
    c = _get(db, candidate_id)
    notifications = (db.query(NotificationRecord).filter(NotificationRecord.candidate_id == c.id)
                     .order_by(NotificationRecord.created_at.desc()).limit(50).all())
    interviews = list(c.interviews)
    latest_eval = next((i.evaluation for i in reversed(interviews) if i.evaluation), None)
    out = candidate_row(c)
    out.update({
        "resume_content_type": c.resume_content_type, "resume_size_bytes": c.resume_size_bytes,
        "resume_profile": c.resume_profile, "consent_version": c.consent_version, "consented_at": c.consented_at,
        "interviews": [interview_row(i) for i in interviews],
        "latest_evaluation": evaluation_out(latest_eval),
        "notifications": [notification_out(n) for n in notifications],
    })
    if include_resume_text:
        out["resume_text"] = c.resume_text
    return out


@router.patch("/{candidate_id}")
def update_candidate(candidate_id: int, data: CandidatePatch, db: Session = Depends(get_db),
                     admin: AdminUser = Depends(require_admin)):
    c = _get(db, candidate_id)
    changes = data.model_dump(exclude_unset=True)
    merged = validate_candidate_fields(
        name=changes.get("name", c.name), email=changes.get("email", c.email or ""),
        phone=changes.get("phone", c.phone or ""), location=changes.get("location", c.location or ""),
        experience_years=changes.get("experience_years", c.experience_years))
    for field in ("name", "email", "phone", "location", "experience_years"):
        if field in changes:
            setattr(c, field, merged[field])
    if "skills" in changes:
        c.skills = clean_skills(changes["skills"])
    if "job_id" in changes:
        if changes["job_id"] is not None and not db.get(Job, changes["job_id"]):
            raise HTTPException(status_code=400, detail="Unknown job")
        c.job_id = changes["job_id"]
    status_changed = False
    if changes.get("interview_status") and changes["interview_status"] != c.interview_status:
        if changes["interview_status"] not in CANDIDATE_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of {list(CANDIDATE_STATUSES)}")
        candidate_service.set_status(db, c, changes["interview_status"])
        status_changed = True
    if data.reset_attempts:
        c.interview_attempts = 0
    c.updated_at = datetime.utcnow()
    db.commit()
    logger.info("Admin %s updated candidate %s", admin.id, c.id)
    return {**candidate_row(c), "status_changed": status_changed}


@router.get("/{candidate_id}/resume")
def candidate_resume(candidate_id: int, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    """Resume access: a short-lived signed URL (Cloud Storage) or the file (local dev). Logged, never public."""
    c = _get(db, candidate_id)
    logger.info("Admin %s opened the resume of candidate %s", admin.id, c.id)
    return file_response(c.resume_path, filename=c.resume_filename or f"resume_{c.id}",
                         media_type=c.resume_content_type, label="Resume")


@router.post("/{candidate_id}/invite")
def invite(candidate_id: int, data: NotifyIn | None = None, db: Session = Depends(get_db),
           admin: AdminUser = Depends(require_admin)):
    """Send the interview invitation (e-mail and/or SMS). Rotates the link secret if the candidate has none."""
    c = _get(db, candidate_id)
    if not c.invite_token:
        c.invite_token = new_token()
    if c.interview_status == "applied":
        candidate_service.set_status(db, c, "invited")
    db.commit()
    records = notification_client.notify_candidate(db, c, "invitation", channels=(data.channels if data else None),
                                                   admin_id=admin.id)
    return {"candidate": candidate_row(c), "notifications": [notification_out(r) for r in records]}


@router.post("/{candidate_id}/notify")
def notify(candidate_id: int, data: NotifyIn, db: Session = Depends(get_db),
           admin: AdminUser = Depends(require_admin)):
    c = _get(db, candidate_id)
    if data.kind not in notification_client.KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {list(notification_client.KINDS)}")
    if any(ch not in notification_client.CHANNELS for ch in (data.channels or [])):
        raise HTTPException(status_code=400, detail="channels must be any of 'email', 'sms', 'call'")
    extra = None
    if data.kind == "custom":
        if not (data.message or "").strip():
            raise HTTPException(status_code=400, detail="Write a message to send.")
        extra = {"message": data.message.strip(), "subject": (data.subject or "").strip()}
    records = notification_client.notify_candidate(db, c, data.kind, channels=data.channels, admin_id=admin.id,
                                                   extra=extra)
    return {"notifications": [notification_out(r) for r in records]}


@router.delete("/{candidate_id}")
def delete_candidate(candidate_id: int, db: Session = Depends(get_db), admin: AdminUser = Depends(require_superadmin)):
    """Permanent deletion of a candidate and everything stored about them (privacy request / retention)."""
    c = _get(db, candidate_id)
    uris = [c.resume_path] + [i.video_path for i in c.interviews]
    for interview in list(c.interviews):
        db.delete(interview)            # cascades: events, turns, evaluation
    db.flush()
    db.query(NotificationRecord).filter(NotificationRecord.candidate_id == c.id).delete()
    db.delete(c)
    db.commit()
    for uri in uris:
        storage.delete_file(uri)
    logger.info("Admin %s deleted candidate %s", admin.id, candidate_id)
    return {"status": "deleted"}
