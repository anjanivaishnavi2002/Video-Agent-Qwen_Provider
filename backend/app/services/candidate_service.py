"""Candidate lifecycle rules shared by the public and admin APIs."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CANDIDATE_STATUSES, Candidate, Interview, InterviewTurn

# A finished interview never moves a candidate backwards over a recruiter decision.
_DECISIONS = {"shortlisted", "rejected", "on_hold"}


def set_status(db: Session, candidate: Candidate, status: str) -> None:
    if status not in CANDIDATE_STATUSES:
        raise ValueError(f"Unknown status '{status}'")
    candidate.interview_status = status
    candidate.updated_at = datetime.utcnow()


def can_start_interview(candidate: Candidate) -> tuple[bool, str]:
    if candidate.interview_status in ("rejected",):
        return False, "This application is closed."
    if (candidate.interview_attempts or 0) >= settings.MAX_INTERVIEW_ATTEMPTS:
        return False, "The maximum number of interview attempts has been reached. Please contact the recruiter."
    return True, ""


def mark_started(db: Session, candidate: Candidate, interview: Interview) -> None:
    candidate.interview_attempts = (candidate.interview_attempts or 0) + 1
    interview.attempt_number = candidate.interview_attempts
    if candidate.interview_status not in _DECISIONS:
        set_status(db, candidate, "in_progress")


def mark_finished(db: Session, candidate: Candidate) -> None:
    if candidate.interview_status not in _DECISIONS:
        set_status(db, candidate, "completed")


def sync_turns(db: Session, interview: Interview, transcript: list[dict]) -> None:
    """Mirror new transcript entries into interview_turns (append-only; the JSON transcript stays the source)."""
    have = db.query(InterviewTurn).filter(InterviewTurn.interview_id == interview.id).count()
    for index, turn in enumerate(transcript):
        if index < have or not turn.get("text"):
            continue
        db.add(InterviewTurn(
            interview_id=interview.id, turn_index=index, role=turn.get("role", "assistant"),
            text=turn["text"], topic=(turn.get("topic") or "")[:200] or None, move=turn.get("move"),
            learned=turn.get("learned") or None,
        ))


def refund_attempt(db: Session, candidate: Candidate) -> None:
    """The interview could not start because of OUR failure (AI outage): the candidate keeps the attempt."""
    candidate.interview_attempts = max(0, (candidate.interview_attempts or 0) - 1)
    if candidate.interview_status == "in_progress":
        candidate.interview_status = "invited" if candidate.interview_attempts else "applied"
    candidate.updated_at = datetime.utcnow()
