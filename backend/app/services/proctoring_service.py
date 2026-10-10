"""Browser/tab-switch rule, enforced on the server (the page cannot skip it):
warning 1, warning 2, and the next switch ends the interview. Works for voice and chat interviews."""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Candidate, Interview, InterviewEvent
from app.services import candidate_service, session_manager

logger = logging.getLogger(__name__)


def record_tab_switch(db: Session, interview_id: int, offset_ms: int | None = None) -> dict:
    """Count one tab/browser switch. Returns {count, warnings_left, ended, warning}."""
    limit = settings.MAX_TAB_SWITCH_WARNINGS
    # Atomic increment: two quick switches (or two instances) cannot both read the same old value.
    updated = (db.query(Interview)
               .filter(Interview.id == interview_id, Interview.status == "running")
               .update({Interview.tab_switch_count: Interview.tab_switch_count + 1}, synchronize_session=False))
    db.commit()
    interview = db.get(Interview, interview_id)
    db.refresh(interview)
    if not updated:                                   # already finished / ended: nothing more to count
        return {"count": interview.tab_switch_count or 0, "warnings_left": 0, "ended": interview.status != "running",
                "warning": False, "limit": limit}
    count = interview.tab_switch_count or 0
    db.add(InterviewEvent(interview_id=interview.id, event_type="tab_hidden", occurred_at=datetime.utcnow(),
                          offset_ms=offset_ms, details={"count": count}))
    if count <= limit:
        db.commit()
        return {"count": count, "warnings_left": limit - count, "ended": False, "warning": True, "limit": limit}

    # Third switch (limit + 1): end it. Voice sessions notice the status change and close themselves.
    interview.status = "ended_early"
    interview.end_reason = "tab_switch_limit"
    interview.ended_at = datetime.utcnow()
    candidate = db.get(Candidate, interview.candidate_id)
    if candidate:
        candidate_service.mark_finished(db, candidate, interview)
    session = session_manager.SESSIONS.pop(interview.id, None)
    if session is not None:
        session.end("tab_switch_limit")
    db.commit()
    logger.info("Interview %s ended: tab switch limit reached", interview.id)
    return {"count": count, "warnings_left": 0, "ended": True, "warning": False, "limit": limit}
