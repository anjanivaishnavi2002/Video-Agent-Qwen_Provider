import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.admin.candidates import candidate_row, evaluation_out, interview_row
from app.api.admin.common import file_response, paginate
from app.db.database import get_db
from app.db.models import AdminUser, Candidate, Interview
from app.providers.llm_errors import LLMError
from app.security import require_admin
from app.services import evaluation_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/interviews", tags=["admin: interviews"])


def _get(db: Session, interview_id: int) -> Interview:
    interview = db.get(Interview, interview_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    return interview


@router.get("")
def list_interviews(
    status: str | None = None, candidate_id: int | None = None, job_id: int | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db), _: AdminUser = Depends(require_admin),
):
    query = db.query(Interview).order_by(Interview.created_at.desc(), Interview.id.desc())
    if status:
        query = query.filter(Interview.status == status)
    if candidate_id:
        query = query.filter(Interview.candidate_id == candidate_id)
    if job_id:
        query = query.filter(Interview.job_id == job_id)
    items, total = paginate(query, page, page_size)
    names = {c.id: c.name for c in db.query(Candidate.id, Candidate.name)
             .filter(Candidate.id.in_({i.candidate_id for i in items})).all()} if items else {}
    rows = [{**interview_row(i), "candidate_name": names.get(i.candidate_id)} for i in items]
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/{interview_id}")
def interview_detail(interview_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    i = _get(db, interview_id)
    candidate = db.get(Candidate, i.candidate_id)
    return {
        **interview_row(i),
        "candidate": candidate_row(candidate) if candidate else None,
        "settings": i.settings_snapshot, "transcript": i.transcript or [], "summary": i.summary,
        "video_size_bytes": i.video_size_bytes, "video_uploaded_at": i.video_uploaded_at,
        "events": [{"type": e.event_type, "timestamp": e.occurred_at, "offset_ms": e.offset_ms,
                    "details": e.details} for e in i.events],
        "evaluation": evaluation_out(i.evaluation),
    }


@router.get("/{interview_id}/recording")
def recording(interview_id: int, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    i = _get(db, interview_id)
    logger.info("Admin %s opened the recording of interview %s", admin.id, i.id)
    ext = (i.video_path or "").rsplit(".", 1)[-1] or "webm"
    return file_response(i.video_path, filename=f"interview_{i.id}.{ext}", media_type=f"video/{ext}", label="Recording")


@router.post("/{interview_id}/evaluate")
def evaluate(interview_id: int, force: bool = True, db: Session = Depends(get_db),
             _: AdminUser = Depends(require_admin)):
    """(Re)run the final AI evaluation, e.g. after a temporary AI outage."""
    i = _get(db, interview_id)
    if i.status not in ("finished", "ended_early"):
        raise HTTPException(status_code=409, detail="The interview has not finished yet.")
    try:
        return evaluation_out(evaluation_service.evaluate_interview(db, interview_id, force=force))
    except LLMError as exc:
        raise HTTPException(status_code=exc.http_status, detail=f"AI evaluation unavailable: {exc.message}") from exc
