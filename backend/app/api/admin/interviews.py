import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.admin.candidates import candidate_row, evaluation_out, interview_row
from app.api.admin.common import file_response, paginate
from app.db.database import get_db
from collections import Counter

from app.config import settings
from app.db.models import AdminUser, Candidate, Interview, InterviewJob, Job
from app.providers.llm_errors import LLMError
from app.security import require_admin
from app.services import chat_service, credit_service, evaluation_service, summary_service

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
    query = (db.query(Interview).filter(Interview.status != "replaced")        # candidate deleted + re-recorded
             .order_by(Interview.created_at.desc(), Interview.id.desc()))
    if status:
        query = query.filter(Interview.status == status)
    if candidate_id:
        query = query.filter(Interview.candidate_id == candidate_id)
    if job_id:
        attached = db.query(InterviewJob.interview_id).filter(InterviewJob.job_id == job_id)
        query = query.filter((Interview.job_id == job_id) | Interview.id.in_(attached))
    items, total = paginate(query, page, page_size)
    names = {c.id: c.name for c in db.query(Candidate.id, Candidate.name)
             .filter(Candidate.id.in_({i.candidate_id for i in items})).all()} if items else {}
    attached = _attached_jobs(db, items)
    rows = [{**interview_row(i), "candidate_name": names.get(i.candidate_id), "jobs": attached.get(i.id, []),
             "job_title": ", ".join(j["title"] for j in attached.get(i.id, [])) or None}
            for i in items]
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


def _attached_jobs(db: Session, items: list[Interview]) -> dict[int, list[dict]]:
    """interview id -> the jobs its recording/report is attached to (applications + the legacy single job)."""
    out: dict[int, list[dict]] = {i.id: [] for i in items}
    if not items:
        return out
    ids = list(out)
    seen: set[tuple[int, int]] = set()
    for iid, jid, title in (db.query(InterviewJob.interview_id, Job.id, Job.title)
                            .join(Job, Job.id == InterviewJob.job_id)
                            .filter(InterviewJob.interview_id.in_(ids)).order_by(InterviewJob.id).all()):
        out[iid].append({"id": jid, "title": title})
        seen.add((iid, jid))
    for i in items:
        if i.job_id and (i.id, i.job_id) not in seen:
            job = db.get(Job, i.job_id)
            if job:
                out[i.id].append({"id": job.id, "title": job.title})
    return out


def _teaser(db: Session, i: Interview, candidate: Candidate | None) -> dict:
    """What an admin may see BEFORE paying credits: the job, the outcome and a few facts - no video, no transcript."""
    jobs = _attached_jobs(db, [i])[i.id]
    job = jobs[0] if jobs else None
    seconds = int((i.ended_at - i.started_at).total_seconds()) if i.ended_at and i.started_at else None
    counts = Counter(e.event_type for e in i.events)
    ev = i.evaluation
    return {
        **interview_row(i), "locked": True, "unlock_cost": settings.UNLOCK_CREDIT_COST,
        "credit_balance": credit_service.balance(db),
        "job": job, "jobs": jobs,
        "candidate": {"id": candidate.id, "name": candidate.name, "experience_years": candidate.experience_years,
                      "skills": (candidate.skills or [])[:8]} if candidate else None,
        "mode": i.mode or "voice", "duration_seconds": seconds,
        "tab_switch_count": i.tab_switch_count or 0, "event_counts": dict(counts),
        "teaser_summary": (ev.summary[:220] + ("..." if len(ev.summary) > 220 else "")) if ev and ev.summary else None,
        "strengths": (ev.strengths or [])[:2] if ev else [], "evaluation": None, "transcript": [], "events": [],
        "chat_work": None, "summary": None, "settings": None,
    }


@router.get("/{interview_id}")
def interview_detail(interview_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    i = _get(db, interview_id)
    candidate = db.get(Candidate, i.candidate_id)
    if not credit_service.is_unlocked(i):
        return _teaser(db, i, candidate)
    return {
        **interview_row(i), "locked": False,
        "candidate": candidate_row(candidate) if candidate else None,
        "mode": i.mode or "voice", "tab_switch_count": i.tab_switch_count or 0,
        "chat_work": chat_service.work_for_admin(i) if i.chat_tasks else None,
        "settings": i.settings_snapshot, "transcript": i.transcript or [], "summary": i.summary,
        "video_size_bytes": i.video_size_bytes, "video_uploaded_at": i.video_uploaded_at,
        "events": [{"type": e.event_type, "timestamp": e.occurred_at, "offset_ms": e.offset_ms,
                    "details": e.details} for e in i.events],
        "evaluation": evaluation_out(i.evaluation),
    }


@router.post("/{interview_id}/unlock")
def unlock(interview_id: int, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    """Spend credits to see the recording and the full report. Safe to call twice: it never charges twice."""
    i = _get(db, interview_id)
    if i.status not in ("finished", "ended_early"):
        raise HTTPException(status_code=409, detail="The interview has not finished yet.")
    result = credit_service.unlock_interview(db, admin, i)
    logger.info("Admin %s unlocked interview %s (charged %s)", admin.id, i.id, result["charged"])
    return result


@router.get("/{interview_id}/recording")
def recording(interview_id: int, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    i = _get(db, interview_id)
    if not credit_service.is_unlocked(i):
        raise HTTPException(status_code=402, detail="Unlock this interview with credits to watch the recording.")
    logger.info("Admin %s opened the recording of interview %s", admin.id, i.id)
    ext = (i.video_path or "").rsplit(".", 1)[-1] or "webm"
    return file_response(i.video_path, filename=f"interview_{i.id}.{ext}", media_type=f"video/{ext}", label="Recording")


@router.post("/{interview_id}/report")
def regenerate_report(interview_id: int, db: Session = Depends(get_db), _: AdminUser = Depends(require_admin)):
    """(Re)build the reviewer report (summary, scores, written-work review), e.g. after a temporary AI outage."""
    i = _get(db, interview_id)
    if i.status not in ("finished", "ended_early"):
        raise HTTPException(status_code=409, detail="The interview has not finished yet.")
    return summary_service.generate_summary(db, interview_id, force=True)


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
