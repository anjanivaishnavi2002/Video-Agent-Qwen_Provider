"""Chat (written skills) assessment endpoints. Same security as the voice interview: the candidate starts it with
the invite token and then proves itself with the per-interview session token."""
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Candidate, Interview
from app.providers.llm_errors import LLMError
from app.security import _same, require_session_token
from app.services import candidate_service, chat_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatStart(BaseModel):
    candidate_id: int
    invite_token: str = Field(min_length=16, max_length=128)
    focus: str | None = Field(default=None, pattern="^(email|chat)$")   # the process the candidate picked


class ChatMessage(BaseModel):
    task_id: str = Field(max_length=20)
    text: str = Field(min_length=1, max_length=8000)


class EmailDraft(BaseModel):
    task_id: str = Field(max_length=20)
    email_id: str | None = Field(default=None, max_length=20)     # which email of the inbox this reply answers
    subject: str = Field(default="", max_length=400)
    body: str = Field(default="", max_length=16000)


def _running_chat(db: Session, interview_id: int, *, exercises_ok: bool = False) -> Interview:
    """The interview must be a written assessment - or, with exercises_ok, a live interview that has exercises."""
    interview = db.get(Interview, interview_id)
    if not interview or ((interview.mode or "voice") != "chat" and not (exercises_ok and interview.chat_tasks)):
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.status != "running":
        raise HTTPException(status_code=409, detail="This assessment has ended.")
    return interview


def _task_or_404(interview: Interview, task_id: str, kind: str) -> dict:
    try:
        task = chat_service._task(interview, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    if task["kind"] != kind:
        raise HTTPException(status_code=400, detail="Wrong task type")
    return task


def _state(interview: Interview) -> dict:
    cfg = interview.settings_snapshot or {}
    return {
        "mode": "chat", "session_id": interview.id, "candidate_id": interview.candidate_id,
        "duration_minutes": cfg.get("duration_minutes", 30),
        "tasks": [chat_service.public_task(t) for t in interview.chat_tasks or []],
        "work": interview.chat_work or {},
        "tab_switch_count": interview.tab_switch_count or 0,
        "max_warnings": settings.MAX_TAB_SWITCH_WARNINGS,
    }


@router.post("/start")
def start_chat(data: ChatStart, db: Session = Depends(get_db)):
    candidate = db.get(Candidate, data.candidate_id)
    if not candidate or not _same(candidate.invite_token, data.invite_token):
        raise HTTPException(status_code=403, detail="This interview link is not valid.")
    allowed, reason = candidate_service.can_start_interview(candidate)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)
    try:
        interview = chat_service.start_chat(db, candidate, data.focus)
    except LLMError as exc:
        logger.error("Could not prepare chat assessment: %s | %s", exc.message, exc.detail)
        raise HTTPException(status_code=exc.http_status, detail=f"AI assessment unavailable: {exc.message}",
                            headers={"Retry-After": "5"} if exc.retryable else None) from exc
    except Exception as exc:
        logger.exception("Could not prepare chat assessment")
        raise HTTPException(status_code=500, detail="Could not prepare the assessment. Please try again.") from exc
    return {**_state(interview), "session_token": interview.access_token}


@router.get("/{session_id}", dependencies=[Depends(require_session_token)])
def get_chat(session_id: int, db: Session = Depends(get_db)):
    return _state(_running_chat(db, session_id))


@router.post("/{session_id}/message", dependencies=[Depends(require_session_token)])
def chat_message(session_id: int, data: ChatMessage, db: Session = Depends(get_db)):
    interview = _running_chat(db, session_id, exercises_ok=True)
    _task_or_404(interview, data.task_id, "chat")
    try:
        _, reply, resolved = chat_service.customer_reply(interview, data.task_id, data.text)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LLMError as exc:
        logger.error("Customer reply failed: %s | %s", exc.message, exc.detail)
        raise HTTPException(status_code=exc.http_status, detail=f"The customer did not answer: {exc.message}") from exc
    db.commit()
    return {"reply": reply, "resolved": resolved}


@router.post("/{session_id}/email", dependencies=[Depends(require_session_token)])
def save_email(session_id: int, data: EmailDraft, db: Session = Depends(get_db)):
    interview = _running_chat(db, session_id, exercises_ok=True)
    _task_or_404(interview, data.task_id, "email")
    try:
        saved = chat_service.save_email(interview, data.task_id, data.subject, data.body, data.email_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return {"saved": True, "saved_at": saved["saved_at"]}


@router.post("/{session_id}/finish", dependencies=[Depends(require_session_token)])
def finish_chat(session_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    interview = db.get(Interview, session_id)
    if not interview or (interview.mode or "voice") != "chat":
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.status == "running":
        interview.status = "finished"
        interview.end_reason = "candidate_ended"
        interview.ended_at = datetime.utcnow()
        candidate = db.get(Candidate, interview.candidate_id)
        if candidate:
            candidate_service.mark_finished(db, candidate, interview)
        db.commit()
        background.add_task(chat_service.notify_completion_in_background, session_id)
    return {"session_id": interview.id, "finished": True}
