"""
Candidate portal: account (e-mail + password), job board, applications.

Every route except register/login needs the candidate's bearer token (checked on the backend). Candidates only ever
see/modify their OWN applications. The interview itself still runs on the per-application invite token + per-session
token, so the existing voice/chat flows are unchanged.
"""
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.resume import clean_skills, process_resume_file, validate_candidate_fields
from app.db.database import get_db
from app.db.models import Candidate, CandidateAccount, Interview, InterviewJob, Job
from app.security import (
    _DUMMY_HASH, check_login_allowed, clear_login_failures, client_ip, create_candidate_token,
    get_current_account, hash_password, new_token, record_login_failure, verify_password,
)
from app.config import settings
from app.services import candidate_service, storage
from app.services.consent_service import get_consent

router = APIRouter(prefix="/portal", tags=["candidate portal"])

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class RegisterIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=72)
    full_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32)


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)


def account_out(a: CandidateAccount) -> dict:
    return {"id": a.id, "email": a.email, "full_name": a.full_name, "phone": a.phone}


def _strong(password: str) -> None:
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise HTTPException(status_code=400, detail="Use at least 8 characters with a letter and a number.")


_registrations: dict[str, deque] = defaultdict(deque)
_registrations_lock = threading.Lock()


def _throttle_registration(ip: str, limit: int = 20, window: int = 3600) -> None:
    """Best effort, per instance (Cloud Armor is the strong layer): at most `limit` sign-ups per IP per hour."""
    now = time.time()
    with _registrations_lock:
        bucket = _registrations[ip]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(status_code=429, detail="Too many sign-ups from this network. Try again later.")
        bucket.append(now)


@router.post("/register", status_code=201)
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    _throttle_registration(client_ip(request))
    email = data.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid e-mail address.")
    _strong(data.password)
    fields = validate_candidate_fields(name=data.full_name, email=email, phone=data.phone or "", location="",
                                       experience_years=None)
    if db.query(CandidateAccount).filter(CandidateAccount.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this e-mail already exists. Please sign in.")
    account = CandidateAccount(email=email, full_name=fields["name"], phone=fields["phone"],
                               password_hash=hash_password(data.password), last_login_at=datetime.utcnow())
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this e-mail already exists. Please sign in.")
    token, expires = create_candidate_token(account)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires, "account": account_out(account)}


@router.post("/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    email, ip = data.email.strip().lower(), client_ip(request)
    check_login_allowed("cand:" + email, ip)
    account = db.query(CandidateAccount).filter(CandidateAccount.email == email).first()
    ok = verify_password(data.password, account.password_hash if account else _DUMMY_HASH)
    if not account or not ok or not account.is_active:
        record_login_failure("cand:" + email, ip)
        raise HTTPException(status_code=401, detail="Incorrect e-mail or password.")
    clear_login_failures("cand:" + email, ip)
    account.last_login_at = datetime.utcnow()
    db.commit()
    token, expires = create_candidate_token(account)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires, "account": account_out(account)}


@router.post("/logout")
def logout(account: CandidateAccount = Depends(get_current_account), db: Session = Depends(get_db)):
    account.token_version += 1          # invalidates every token issued so far
    db.commit()
    return {"status": "signed_out"}


@router.get("/me")
def me(account: CandidateAccount = Depends(get_current_account)):
    return account_out(account)


def _job_card(job: Job, application: Candidate | None = None) -> dict:
    return {"id": job.id, "title": job.title, "department": job.department, "location": job.location,
            "employment_type": job.employment_type, "description": job.description,
            "required_skills": job.required_skills or [], "has_jd_file": bool(job.jd_file_path),
            "process_type": job.process_type, "experience_min": job.experience_min,
            "experience_max": job.experience_max,
            "applied": application is not None,
            "application_status": application.interview_status if application else None}


@router.get("/jobs")
def jobs(account: CandidateAccount = Depends(get_current_account), db: Session = Depends(get_db)):
    open_jobs = db.query(Job).filter(Job.status == "open").order_by(Job.created_at.desc(), Job.id.desc()).all()
    mine = {c.job_id: c for c in db.query(Candidate).filter(Candidate.account_id == account.id).all() if c.job_id}
    return [_job_card(j, mine.get(j.id)) for j in open_jobs]


def _application_out(c: Candidate, db: Session) -> dict:
    job = db.get(Job, c.job_id) if c.job_id else None
    allowed, reason = candidate_service.can_start_interview(c)
    attached = candidate_service.attached_interview(db, c)
    have_any = bool(candidate_service.account_interviews(db, c))
    return {"candidate_id": c.id, "name": c.name, "invite_token": c.invite_token, "status": c.interview_status,
            "job": _job_card(job) if job else None,
            "can_start": allowed, "message": reason or None, "attempts": c.interview_attempts,
            "applied_at": c.created_at, "score_visible": False,
            "interview_id": attached.id if attached else None,
            "interview_attached": attached is not None,
            "interview_done": bool(attached and attached.status != "running"),
            "has_interviews": have_any,
            "interviews": [_interview_out(attached, db)] if attached else []}


def _interview_out(i: Interview, db: Session) -> dict:
    seconds = int((i.ended_at - i.started_at).total_seconds()) if i.ended_at and i.started_at else None
    jobs = (db.query(InterviewJob.job_id, InterviewJob.candidate_id).filter(InterviewJob.interview_id == i.id).all())
    titles = {j.id: j.title for j in db.query(Job).filter(Job.id.in_([x[0] for x in jobs])).all()} if jobs else {}
    return {"id": i.id, "mode": i.mode or "voice", "status": i.status, "started_at": i.started_at,
            "duration_seconds": seconds, "tab_switches": i.tab_switch_count or 0,
            "has_recording": bool(i.video_path),
            "attached_to": [{"job_id": jid, "job_title": titles.get(jid), "application_id": cid} for jid, cid in jobs]}


@router.get("/applications")
def applications(account: CandidateAccount = Depends(get_current_account), db: Session = Depends(get_db)):
    rows = (db.query(Candidate).filter(Candidate.account_id == account.id)
            .order_by(Candidate.created_at.desc(), Candidate.id.desc()).all())
    return [_application_out(c, db) for c in rows]


@router.post("/jobs/{job_id}/apply", status_code=201)
def apply(
    job_id: int,
    consent_version: str = Form(...),
    phone: str = Form(""),
    location: str = Form(""),
    experience_years: float | None = Form(None),
    skills: str = Form(""),
    interview_id: int | None = Form(None),       # which of my interviews to attach to this job
    new_interview: bool = Form(False),            # or: I will record a new one for this job
    file: UploadFile | None = File(None),
    account: CandidateAccount = Depends(get_current_account),
    db: Session = Depends(get_db),
):
    """Apply to one open job. The resume is uploaded once; later applications may reuse the latest one."""
    if consent_version != get_consent()["version"]:
        raise HTTPException(status_code=400, detail="Please read and accept the current consent form first.")
    job = db.get(Job, job_id)
    if not job or job.status != "open":
        raise HTTPException(status_code=400, detail="This job is not open for applications.")
    existing = db.query(Candidate).filter(Candidate.account_id == account.id, Candidate.job_id == job_id).first()
    if existing:
        return _application_out(existing, db)          # applying twice just returns the application

    fields = validate_candidate_fields(name=account.full_name, email=account.email,
                                       phone=phone or account.phone or "", location=location,
                                       experience_years=experience_years)
    previous = (db.query(Candidate).filter(Candidate.account_id == account.id, Candidate.resume_path.isnot(None))
                .order_by(Candidate.id.desc()).first())
    if file is not None and file.filename:
        resume_fields = process_resume_file(file)
    elif previous:
        resume_fields = {k: getattr(previous, k) for k in (
            "resume_filename", "resume_path", "resume_content_type", "resume_size_bytes", "resume_text")}
    else:
        raise HTTPException(status_code=400, detail="Please upload your resume to apply.")

    candidate = Candidate(**fields, **resume_fields, skills=clean_skills(skills), job_id=job.id,
                          account_id=account.id, consent_version=consent_version, consented_at=datetime.utcnow(),
                          interview_status="applied", invite_token=new_token(),
                          resume_profile=previous.resume_profile if (previous and not (file and file.filename))
                          else None)
    db.add(candidate)
    db.flush()
    if interview_id is not None:                      # the candidate chose one of their interviews
        chosen = _my_interview(db, account, interview_id)
        if chosen.status not in ("finished", "ended_early"):
            raise HTTPException(status_code=409, detail="That interview is not finished yet.")
        candidate_service.attach_interview(db, chosen, candidate)
    elif not new_interview:
        latest = candidate_service.account_interview(db, candidate)
        if latest:      # no choice made: the newest interview (they can change it in Settings)
            candidate_service.attach_interview(db, latest, candidate)
    db.commit()
    db.refresh(candidate)
    return _application_out(candidate, db)


def _my_interview(db: Session, account: CandidateAccount, interview_id: int) -> Interview:
    row = (db.query(Interview).join(Candidate, Candidate.id == Interview.candidate_id)
           .filter(Interview.id == interview_id, Candidate.account_id == account.id).first())
    if not row or row.status == "replaced":
        raise HTTPException(status_code=404, detail="Interview not found.")
    return row


@router.get("/interviews")
def my_interviews(account: CandidateAccount = Depends(get_current_account), db: Session = Depends(get_db)):
    """My interviews (Settings page): what each one is attached to, so I can choose per job."""
    mine = db.query(Candidate).filter(Candidate.account_id == account.id).all()
    if mine:
        candidate_service.abandon_stale_interviews(db, mine[0])
    rows = (db.query(Interview).join(Candidate, Candidate.id == Interview.candidate_id)
            .filter(Candidate.account_id == account.id, Interview.status.in_(("running", "finished", "ended_early")))
            .order_by(Interview.id.desc()).all())
    can, reason = candidate_service.can_start_interview(mine[0]) if mine else (False, "Apply to a job first.")
    return {"interviews": [_interview_out(i, db) for i in rows], "can_record_new": can, "message": reason or None,
            "max_interviews": settings.MAX_INTERVIEW_ATTEMPTS,
            "applications": [_application_out(a, db) for a in mine]}


class AttachIn(BaseModel):
    interview_id: int | None = None      # None = no interview attached to this application


@router.put("/applications/{candidate_id}/interview")
def attach_to_application(candidate_id: int, data: AttachIn,
                          account: CandidateAccount = Depends(get_current_account), db: Session = Depends(get_db)):
    """Choose which of MY interviews is attached to this job application (the recruiter sees that recording)."""
    app = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.account_id == account.id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found.")
    if data.interview_id is None:
        candidate_service.detach_interview(db, app)
    else:
        interview = _my_interview(db, account, data.interview_id)
        if interview.status == "running":
            raise HTTPException(status_code=409, detail="Finish this interview before attaching it.")
        candidate_service.attach_interview(db, interview, app)
    db.commit()
    return _application_out(app, db)


@router.delete("/interviews/{interview_id}")
def delete_my_interview(interview_id: int, account: CandidateAccount = Depends(get_current_account),
                        db: Session = Depends(get_db)):
    """Delete one of my recordings (and its report). Applications it was attached to fall back to another of my
    interviews, or to none. The row stays as "replaced" because credit history refers to it."""
    interview = _my_interview(db, account, interview_id)
    if interview.status == "running":
        raise HTTPException(status_code=409, detail="Finish or end your current interview before deleting it.")
    mine = db.query(Candidate).filter(Candidate.account_id == account.id).all()
    linked = [x[0] for x in db.query(InterviewJob.candidate_id).filter(InterviewJob.interview_id == interview.id)]
    uri = interview.video_path
    interview.status = "replaced"
    interview.video_path = interview.video_size_bytes = interview.video_uploaded_at = None
    interview.summary = interview.chat_work = interview.chat_tasks = None
    interview.transcript = []
    if interview.evaluation is not None:
        db.delete(interview.evaluation)
    db.query(InterviewJob).filter(InterviewJob.interview_id == interview.id).delete()
    db.flush()
    fallback = candidate_service.account_interview(db, mine[0]) if mine else None
    for app in mine:
        if app.id in linked:
            candidate_service.detach_interview(db, app)
            if fallback:
                candidate_service.attach_interview(db, fallback, app)
    db.commit()
    if uri:
        try:
            storage.delete_file(uri)
        except Exception:      # the DB no longer points at it; a stray object must not fail the request
            pass
    return my_interviews(account, db)
