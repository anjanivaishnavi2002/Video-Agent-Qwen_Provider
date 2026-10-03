"""Keeps live InterviewSession objects in memory and mirrors them to the database."""
import logging
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Candidate, Interview
from app.providers.gemini_provider import get_llm
from app.services.interview_service import InterviewSession, InterviewSettings
from app.services.resume_service import analyze_resume, resume_text_for_prompt

logger = logging.getLogger(__name__)

# Live sessions. If the server restarts they are rebuilt from the database (see get_session).
SESSIONS: dict[int, InterviewSession] = {}


def _prompt_resume_text(candidate: Candidate) -> str | None:
    text = candidate.resume_text or ""
    full = resume_text_for_prompt(text)
    if full is not None:
        return full
    # Long resume: the merged profile carries the content. If analysis failed there
    # is nothing else to rely on, so fall back to the leading part of the text.
    if not candidate.resume_profile:
        return text[: settings.RESUME_PROMPT_MAX_CHARS]
    return None


def ensure_resume_profile(db: Session, candidate: Candidate) -> None:
    """Analyse the resume once and cache the structured profile on the candidate."""
    if candidate.resume_profile:
        return
    profile = analyze_resume(candidate.resume_text or "", get_llm())
    if profile:
        candidate.resume_profile = profile
        db.commit()


def create_session(db: Session, candidate: Candidate) -> tuple[Interview, InterviewSession]:
    ensure_resume_profile(db, candidate)

    cfg = InterviewSettings.from_config()
    interview = Interview(
        candidate_id=candidate.id,
        status="running",
        transcript=[],
        settings_snapshot=cfg.to_dict(),
        started_at=datetime.utcnow(),
        access_token=secrets.token_urlsafe(32),
    )
    db.add(interview)
    db.commit()
    db.refresh(interview)

    session = InterviewSession(
        cfg,
        candidate.name,
        candidate.resume_profile,
        _prompt_resume_text(candidate),
        started_at=interview.started_at,
    )
    SESSIONS[interview.id] = session
    return interview, session


def get_session(db: Session, interview_id: int) -> InterviewSession | None:
    """Live session, or one rebuilt from the database if the server restarted mid-interview."""
    session = SESSIONS.get(interview_id)
    if session:
        return session

    interview = db.get(Interview, interview_id)
    if not interview or interview.status != "running":
        return None
    candidate = db.get(Candidate, interview.candidate_id)
    if not candidate:
        return None

    session = InterviewSession(
        InterviewSettings.from_dict(interview.settings_snapshot),
        candidate.name,
        candidate.resume_profile,
        _prompt_resume_text(candidate),
        transcript=interview.transcript or [],
        started_at=interview.started_at,
    )
    SESSIONS[interview_id] = session
    logger.info("Restored interview session %s from the database", interview_id)
    return session


def persist(db: Session, interview_id: int, session: InterviewSession, *, close: str | None = None) -> None:
    """Save the transcript after every turn; `close` = final status ('finished' / 'ended_early')."""
    interview = db.get(Interview, interview_id)
    if not interview:
        return
    interview.transcript = session.public_transcript()
    if close:
        interview.status = close
        interview.end_reason = session.end_reason
        interview.ended_at = datetime.utcnow()
        SESSIONS.pop(interview_id, None)
    db.commit()
