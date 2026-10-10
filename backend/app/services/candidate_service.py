"""Candidate lifecycle rules shared by the public and admin APIs."""
from datetime import datetime

from sqlalchemy.orm import Session, object_session

from app.config import settings
from app.db.models import CANDIDATE_STATUSES, Candidate, Interview, InterviewJob, InterviewTurn, Job

# A finished interview never moves a candidate backwards over a recruiter decision.
_DECISIONS = {"shortlisted", "rejected", "on_hold"}


def set_status(db: Session, candidate: Candidate, status: str) -> None:
    if status not in CANDIDATE_STATUSES:
        raise ValueError(f"Unknown status '{status}'")
    candidate.interview_status = status
    candidate.updated_at = datetime.utcnow()


def account_interviews(db: Session, candidate: Candidate) -> list[Interview]:
    """Every finished interview of a signed-in candidate (any of their applications), newest first."""
    if not candidate.account_id:
        return []
    return (db.query(Interview).join(Candidate, Candidate.id == Interview.candidate_id)
            .filter(Candidate.account_id == candidate.account_id,
                    Interview.status.in_(("finished", "ended_early")))
            .order_by(Interview.id.desc()).all())


def account_interview(db: Session, candidate: Candidate) -> Interview | None:
    """The newest finished interview of the account (None for invite links)."""
    rows = account_interviews(db, candidate)
    return rows[0] if rows else None


def attached_interview(db: Session, application: Candidate) -> Interview | None:
    """The interview currently attached to this application (job)."""
    row = (db.query(InterviewJob).filter(InterviewJob.candidate_id == application.id)
           .order_by(InterviewJob.id.desc()).first())
    return db.get(Interview, row.interview_id) if row else None


def attach_interview(db: Session, interview: Interview, application: Candidate) -> None:
    """Make `interview` THE interview (recording + report) of this application. Replaces what was attached before."""
    if not application.job_id:
        return
    db.query(InterviewJob).filter(InterviewJob.candidate_id == application.id).delete()
    db.query(InterviewJob).filter(InterviewJob.interview_id == interview.id,
                                  InterviewJob.job_id == application.job_id).delete()
    db.add(InterviewJob(interview_id=interview.id, job_id=application.job_id, candidate_id=application.id))
    db.flush()
    owner = db.get(Candidate, interview.candidate_id)
    if interview.status in ("finished", "ended_early") and application.interview_status not in _DECISIONS:
        set_status(db, application, "completed")
    if interview.evaluation and interview.evaluation.overall_score is not None:
        application.interview_score = interview.evaluation.overall_score
    elif owner and owner.id != application.id:
        application.interview_score = owner.interview_score


def detach_interview(db: Session, application: Candidate) -> None:
    db.query(InterviewJob).filter(InterviewJob.candidate_id == application.id).delete()
    application.interview_score = None
    if application.interview_status in ("completed", "in_progress"):
        application.interview_status = "applied"


def link_interview(db: Session, interview: Interview, candidate: Candidate) -> None:
    """Invite-link candidates: the interview belongs to their one job right away. Signed-in candidates get theirs
    attached when it finishes (attach_default) and can re-assign it in Settings."""
    if not candidate.account_id and candidate.job_id:
        attach_interview(db, interview, candidate)


def attach_default(db: Session, interview: Interview, candidate: Candidate) -> None:
    """A finished interview goes to every application of the account that has no interview yet."""
    if not candidate.account_id:
        return
    for app in db.query(Candidate).filter(Candidate.account_id == candidate.account_id).all():
        if app.job_id and attached_interview(db, app) is None:
            attach_interview(db, interview, app)


STALE_EMPTY_MINUTES = 2      # a "running" interview with no conversation at all (connection failed)
STALE_MINUTES = 15           # a "running" interview nobody has touched for this long was abandoned


def abandon_stale_interviews(db: Session, candidate: Candidate) -> None:
    """Close interviews left "running" by a crashed/closed tab so they stop blocking the candidate.

    An attempt that never got a single answer (e.g. the AI connection failed) is not counted against the limit."""
    query = db.query(Interview).join(Candidate, Candidate.id == Interview.candidate_id).filter(
        Interview.status == "running")
    query = (query.filter(Candidate.account_id == candidate.account_id) if candidate.account_id
             else query.filter(Interview.candidate_id == candidate.id))
    now = datetime.utcnow()
    changed = False
    for row in query.all():
        touched = row.updated_at or row.started_at or row.created_at or now
        empty = not row.transcript
        age = (now - touched).total_seconds() / 60
        if age < (STALE_EMPTY_MINUTES if empty else STALE_MINUTES):
            continue
        row.status, row.end_reason, row.ended_at = "failed", "abandoned", now
        owner = db.get(Candidate, row.candidate_id)
        if empty and owner and (owner.interview_attempts or 0) > 0:
            owner.interview_attempts -= 1
        if owner and owner.interview_status == "in_progress" and not owner.interview_score:
            owner.interview_status = "applied"
        changed = True
    if changed:
        db.commit()


MAX_ACCOUNT_INTERVIEWS_MESSAGE = ("You already have {n} interviews, the maximum. Delete one in Settings to record "
                                   "a new one.")


def can_start_interview(candidate: Candidate) -> tuple[bool, str]:
    if candidate.interview_status in ("rejected",):
        return False, "This application is closed."
    db = object_session(candidate)
    if db is not None:
        abandon_stale_interviews(db, candidate)
    if candidate.account_id and db is not None:
        count = (db.query(Interview).join(Candidate, Candidate.id == Interview.candidate_id)
                 .filter(Candidate.account_id == candidate.account_id,
                         Interview.status.in_(("running", "finished", "ended_early"))).count())
        if count >= settings.MAX_INTERVIEW_ATTEMPTS:
            return False, MAX_ACCOUNT_INTERVIEWS_MESSAGE.format(n=count)
        return True, ""
    if (candidate.interview_attempts or 0) >= settings.MAX_INTERVIEW_ATTEMPTS:
        return False, "The maximum number of interview attempts has been reached. Please contact the recruiter."
    return True, ""


def mark_started(db: Session, candidate: Candidate, interview: Interview) -> None:
    candidate.interview_attempts = (candidate.interview_attempts or 0) + 1
    interview.attempt_number = candidate.interview_attempts
    if candidate.interview_status not in _DECISIONS:
        set_status(db, candidate, "in_progress")
    db.flush()
    link_interview(db, interview, candidate)


def sync_applications(db: Session, interview: Interview, candidate: Candidate) -> None:
    """Every OTHER application this interview is attached to gets its status and score."""
    for link in db.query(InterviewJob).filter(InterviewJob.interview_id == interview.id,
                                              InterviewJob.candidate_id != candidate.id).all():
        other = db.get(Candidate, link.candidate_id)
        if not other:
            continue
        if other.interview_status not in _DECISIONS and candidate.interview_status in ("completed", "in_progress"):
            set_status(db, other, candidate.interview_status)
        if candidate.interview_score is not None:
            other.interview_score = candidate.interview_score


def mark_finished(db: Session, candidate: Candidate, interview: Interview | None = None) -> None:
    if candidate.interview_status not in _DECISIONS:
        set_status(db, candidate, "completed")
    interview = interview or (db.query(Interview).filter(Interview.candidate_id == candidate.id,
                              Interview.status.in_(("finished", "ended_early"))).order_by(Interview.id.desc()).first())
    if interview is not None:
        attach_default(db, interview, candidate)
        sync_applications(db, interview, candidate)


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


# ---- what the interviewer should focus on (signed-in candidates interview once, for ALL the jobs they applied to) --
def applied_jobs(db: Session, candidate: Candidate) -> list[Job]:
    if candidate.account_id:
        ids = [c.job_id for c in db.query(Candidate).filter(Candidate.account_id == candidate.account_id).all()
               if c.job_id]
        return db.query(Job).filter(Job.id.in_(ids)).order_by(Job.id).all() if ids else []
    return [db.get(Job, candidate.job_id)] if candidate.job_id and db.get(Job, candidate.job_id) else []


def written_kinds(jobs: list[Job]) -> list[str]:
    """Which written exercises fit the roles: chat support -> chat, email support -> email, blended -> both."""
    kinds: list[str] = []
    for job in jobs:
        text = f"{job.title} {job.process_type or ''}".lower()
        wanted = []
        if job.process_type in ("chat", "blended") or "chat" in text:
            wanted.append("chat")
        if job.process_type in ("email", "blended", "back_office") or "email" in text or "e-mail" in text:
            wanted.append("email")
        for kind in wanted:
            if kind not in kinds:
                kinds.append(kind)
    return kinds


def experience_level(years: float | None) -> str:
    if years is None or years < 1:
        return "fresher"
    if years < 3:
        return "junior"
    if years < 5:
        return "mid-level"
    return "senior"


def interview_focus(db: Session, candidate: Candidate) -> dict:
    jobs = applied_jobs(db, candidate)
    years = candidate.experience_years
    return {"jobs": jobs, "kinds": written_kinds(jobs), "years": years, "level": experience_level(years)}


def job_context(db: Session, candidate: Candidate) -> dict | None:
    """The 'job' the interviewer works from: the roles the candidate applied to (one interview serves them all)."""
    focus = interview_focus(db, candidate)
    jobs = focus["jobs"]
    if not jobs:
        return None
    if not candidate.account_id:
        job = jobs[0]
        return {"title": job.title, "description": job.description, "location": job.location,
                "skills": job.required_skills or []}
    skills: list[str] = []
    for job in jobs:
        skills += [x for x in (job.required_skills or []) if x not in skills]
    roles = "\n".join(f"- {j.title} ({j.process_type or 'support'} process"
                      f"{', ' + str(j.experience_min) + '+ yrs' if j.experience_min is not None else ''}): "
                      f"{(j.description or '')[:300]}" for j in jobs[:6])
    kinds = " and ".join(focus["kinds"]) or "voice"
    years = "unknown" if focus["years"] is None else f"{focus['years']:g}"
    return {"title": " / ".join(j.title for j in jobs[:3]),
            "description": (f"The candidate applied to these roles; one interview covers all of them:\n{roles}\n"
                            f"Process focus: {kinds}. Candidate level: {focus['level']} ({years} years of experience), "
                            "so pitch the difficulty of your questions and of any practical exercise to that level."),
            "location": None, "skills": skills[:20]}
