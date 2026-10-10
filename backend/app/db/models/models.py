"""
Database schema.

Large files (resumes, recordings) are NEVER stored here - only their object path / gs:// reference and metadata.
Schema changes go through Alembic (backend/alembic/versions); see docs/DEPLOYMENT.md.
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.database import Base

# Candidate.interview_status values
CANDIDATE_STATUSES = (
    "applied",        # resume uploaded, no interview yet
    "invited",        # an invitation was sent
    "in_progress",    # an interview is running
    "completed",      # interview finished and evaluated (or at least finished)
    "shortlisted",    # recruiter decisions
    "rejected",
    "on_hold",
)


class AdminUser(Base):
    """Recruiters / administrators. Completely separate from candidates."""

    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    full_name = Column(String(200))
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False, default="recruiter")   # admin | recruiter
    is_active = Column(Boolean, nullable=False, default=True)
    token_version = Column(Integer, nullable=False, default=0)       # bump to invalidate every issued token (logout)
    last_login_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    department = Column(String(120))
    location = Column(String(200))
    employment_type = Column(String(50))                 # full_time | part_time | contract ...
    description = Column(Text, nullable=False)           # the job description given to the AI interviewer
    required_skills = Column(JSON)                       # ["English", "CRM tools", ...]
    process_type = Column(String(20))                    # voice | chat | email | blended | back_office | other
    experience_min = Column(Integer)                     # years, None = no minimum
    experience_max = Column(Integer)                     # years, None = no maximum
    status = Column(String(20), nullable=False, default="open", index=True)   # draft | open | closed
    # Optional JD document (PDF/DOCX/TXT): stored in the private bucket, only the reference lives here
    jd_file_path = Column(String)
    jd_file_name = Column(String(200))
    jd_content_type = Column(String(100))
    jd_size_bytes = Column(Integer)
    created_by = Column(Integer, ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidates = relationship("Candidate", back_populates="job")


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String(255), index=True)
    phone = Column(String(32))
    location = Column(String(200))
    experience_years = Column(Float)                      # total years of experience (self reported / resume)
    skills = Column(JSON)                                 # list of skills

    # Resume: the file lives in Cloud Storage (gs://...) in production; only the reference is stored here.
    resume_filename = Column(String)                      # original file name (display only)
    resume_path = Column(String)                          # gs://bucket/path (production) or local path (dev)
    resume_content_type = Column(String(100))
    resume_size_bytes = Column(Integer)
    resume_text = Column(Text)                            # full extracted text
    resume_profile = Column(JSON)                         # structured profile, built once and cached

    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), index=True)   # applied job
    account_id = Column(Integer, ForeignKey("candidate_accounts.id", ondelete="SET NULL"), index=True)  # login owner
    interview_status = Column(String(30), nullable=False, default="applied", index=True)
    interview_attempts = Column(Integer, nullable=False, default=0)
    interview_score = Column(Float)                       # 0-100, copied from the latest evaluation
    invite_token = Column(String(64), unique=True, index=True)   # secret in the candidate's link / start request

    consent_version = Column(String)                      # version of the consent form the candidate accepted
    consented_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    job = relationship("Job", back_populates="candidates")
    interviews = relationship("Interview", back_populates="candidate", order_by="Interview.id")


class Interview(Base):
    """One interview session (an "attempt"). The AI decides every question; there is no fixed list."""

    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    attempt_number = Column(Integer, nullable=False, default=1)
    status = Column(String, default="created", index=True)  # created / running / finished / ended_early / failed
    access_token = Column(String)               # secret the candidate's browser must present
    end_reason = Column(String)                 # completed / time_limit / max_turns / candidate_ended / unresponsive
    settings_snapshot = Column(JSON)            # the exact settings this interview ran with
    transcript = Column(JSON, default=list)     # [{role, text, topic, move, ts, ...}]  (also mirrored in interview_turns)
    started_at = Column(DateTime)
    ended_at = Column(DateTime)

    # Camera + microphone recording of the whole interview (a reference, not the file)
    video_path = Column(String)
    video_size_bytes = Column(Integer)
    video_uploaded_at = Column(DateTime)
    summary = Column(JSON)                      # factual summary + recording/face-event report (see summary_service)
    video_unlocked_at = Column(DateTime)        # set when an admin paid credits to see the recording / full detail
    video_unlocked_by = Column(Integer, ForeignKey("admin_users.id", ondelete="SET NULL"))

    # Chat (written skills) assessment + proctoring counters
    mode = Column(String(10), nullable=False, default="voice")   # voice | chat
    tab_switch_count = Column(Integer, nullable=False, default=0)  # browser/tab switches (2 warnings, the 3rd ends it)
    chat_tasks = Column(JSON)                   # tasks the AI set from the job description + experience
    chat_work = Column(JSON)                    # the candidate's answers per task id

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="interviews")
    events = relationship(
        "InterviewEvent", back_populates="interview", order_by="InterviewEvent.id", cascade="all, delete-orphan",
    )
    turns = relationship(
        "InterviewTurn", back_populates="interview", order_by="InterviewTurn.turn_index", cascade="all, delete-orphan",
    )
    evaluation = relationship("Evaluation", back_populates="interview", uselist=False, cascade="all, delete-orphan")


class InterviewTurn(Base):
    """One message of the conversation (queryable copy of Interview.transcript)."""

    __tablename__ = "interview_turns"
    __table_args__ = (UniqueConstraint("interview_id", "turn_index", name="uq_interview_turn_index"),)

    id = Column(Integer, primary_key=True)
    interview_id = Column(Integer, ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, index=True)
    turn_index = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)        # assistant | candidate
    text = Column(Text, nullable=False)
    topic = Column(String(200))
    move = Column(String(40))
    learned = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    interview = relationship("Interview", back_populates="turns")


class InterviewEvent(Base):
    """
    Observable monitoring events (face missing, multiple faces, ...).
    Never emotions, confidence, honesty or any other inference.
    """

    __tablename__ = "interview_events"

    id = Column(Integer, primary_key=True, index=True)
    interview_id = Column(Integer, ForeignKey("interviews.id"), index=True, nullable=False)
    event_type = Column(String, nullable=False)
    occurred_at = Column(DateTime, nullable=False)   # wall-clock time from the browser
    offset_ms = Column(Integer)                      # ms since the recording started -> sync with the video
    details = Column(JSON)                           # e.g. {"faces": 2}
    created_at = Column(DateTime, default=datetime.utcnow)

    interview = relationship("Interview", back_populates="events")


class Evaluation(Base):
    """Final AI evaluation of one interview (decision support for a human; never an automatic decision)."""

    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True)
    interview_id = Column(Integer, ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, unique=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    overall_score = Column(Float)                    # 0-100
    recommendation = Column(String(30))              # strong_fit | fit | borderline | not_a_fit | insufficient_data
    summary = Column(Text)
    strengths = Column(JSON)                         # [str]
    concerns = Column(JSON)                          # [str]
    criteria = Column(JSON)                          # [{name, score, evidence}]
    model = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    interview = relationship("Interview", back_populates="evaluation")


class NotificationRecord(Base):
    """Every e-mail / SMS the platform asked the notification service to send."""

    __tablename__ = "notification_records"
    __table_args__ = (Index("ix_notification_candidate_created", "candidate_id", "created_at"),)

    id = Column(Integer, primary_key=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    interview_id = Column(Integer, ForeignKey("interviews.id", ondelete="SET NULL"))
    channel = Column(String(10), nullable=False)                 # email | sms
    kind = Column(String(30), nullable=False)                    # invitation | reminder | completion | status_update
    recipient = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)   # queued | sent | failed | skipped
    provider = Column(String(40))
    provider_message_id = Column(String(200))
    error = Column(Text)
    triggered_by = Column(Integer, ForeignKey("admin_users.id", ondelete="SET NULL"))   # null = automatic
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime)


class CandidateAccount(Base):
    """A candidate's login (e-mail + password). Separate from admins. Each application is a Candidate row."""

    __tablename__ = "candidate_accounts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    full_name = Column(String(200), nullable=False)
    phone = Column(String(32))
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    token_version = Column(Integer, nullable=False, default=0)
    last_login_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class CreditWallet(Base):
    """The company's credit balance (single row, id=1). Locked with SELECT ... FOR UPDATE while credits move."""

    __tablename__ = "credit_wallet"

    id = Column(Integer, primary_key=True)
    balance = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreditLedger(Base):
    """Append-only record of every credit movement. `unlock_key` makes an unlock idempotent (unique per interview)."""

    __tablename__ = "credit_ledger"

    id = Column(Integer, primary_key=True)
    delta = Column(Integer, nullable=False)                  # + grant, - unlock
    reason = Column(String(30), nullable=False)              # grant | unlock | adjustment
    note = Column(String(300))
    interview_id = Column(Integer, ForeignKey("interviews.id", ondelete="SET NULL"), index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="SET NULL"), index=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id", ondelete="SET NULL"))
    balance_after = Column(Integer, nullable=False)
    unlock_key = Column(String(60), unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class InterviewJob(Base):
    """Which jobs an interview is attached to. A candidate interviews ONCE (from their resume); every job they apply
    to gets the same recording and report attached, and an admin unlocking it pays only once."""

    __tablename__ = "interview_jobs"
    __table_args__ = (UniqueConstraint("interview_id", "job_id", name="uq_interview_job"),)

    id = Column(Integer, primary_key=True)
    interview_id = Column(Integer, ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)   # the application
    created_at = Column(DateTime, default=datetime.utcnow)
