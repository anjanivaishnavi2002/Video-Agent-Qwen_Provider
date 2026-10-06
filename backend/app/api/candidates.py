"""Public (candidate-facing) lookups: open jobs, and the invitation link."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Candidate, Job
from app.security import _same
from app.services import candidate_service

router = APIRouter(tags=["candidates"])


@router.get("/jobs")
def list_open_jobs(db: Session = Depends(get_db)):
    """Open positions a candidate can apply for (public, limited fields)."""
    jobs = db.query(Job).filter(Job.status == "open").order_by(Job.title).all()
    return [{"id": j.id, "title": j.title, "department": j.department, "location": j.location,
             "employment_type": j.employment_type} for j in jobs]


@router.get("/candidates/invite/{invite_token}")
def invitation(invite_token: str, db: Session = Depends(get_db)):
    """What the candidate's invitation link resolves to. The token is the secret; nothing else is exposed."""
    candidate = db.query(Candidate).filter(Candidate.invite_token == invite_token).first()
    if not candidate or not _same(candidate.invite_token, invite_token):
        raise HTTPException(status_code=404, detail="This invitation link is not valid.")
    allowed, reason = candidate_service.can_start_interview(candidate)
    job = db.get(Job, candidate.job_id) if candidate.job_id else None
    return {"candidate_id": candidate.id, "name": candidate.name, "job_title": job.title if job else None,
            "can_start": allowed, "message": reason or None}
