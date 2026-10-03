from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    resume_filename = Column(String)          # original file name (display only)
    resume_path = Column(String)
    resume_text = Column(Text)                # full extracted text
    resume_profile = Column(JSON)             # structured profile, built once and cached
    consent_version = Column(String)          # version of the consent form the candidate accepted
    consented_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class Interview(Base):
    """
    One interview session. There is deliberately no job here: the interview
    is driven by the candidate's resume + the configured BPO context.
    """

    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    status = Column(String, default="created")  # created / running / finished / ended_early
    access_token = Column(String)               # secret the candidate's browser must present
    end_reason = Column(String)                 # completed / time_limit / max_turns / candidate_ended / unresponsive
    settings_snapshot = Column(JSON)            # the exact settings this interview ran with
    transcript = Column(JSON, default=list)     # [{role, text, topic, move, ts, ...}]
    started_at = Column(DateTime)
    ended_at = Column(DateTime)

    # Camera + microphone recording of the whole interview
    video_path = Column(String)
    video_size_bytes = Column(Integer)
    video_uploaded_at = Column(DateTime)
    summary = Column(JSON)                      # factual summary + recording/face-event report (see summary_service)

    created_at = Column(DateTime, default=datetime.utcnow)

    events = relationship(
        "InterviewEvent",
        back_populates="interview",
        order_by="InterviewEvent.id",
        cascade="all, delete-orphan",
    )


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
