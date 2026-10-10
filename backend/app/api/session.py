import base64
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import Candidate, Interview, InterviewEvent
from app.providers.llm_errors import LLMError
from app.security import _same, caller_role, require_session_token
from app.services import candidate_service, evaluation_service, session_manager, storage, voice_service
from app.services import chat_service, proctoring_service, summary_service
from app.services.interview_service import InterviewSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["session"])

CHUNK_SIZE = 1024 * 1024  # 1 MB


# ---------------------------------------------------------
# Request models
# ---------------------------------------------------------

class StartRequest(BaseModel):
    candidate_id: int
    invite_token: str = Field(min_length=16, max_length=128)   # secret from the application / invitation link


class EndRequest(BaseModel):
    reason: str = "candidate_ended"


class EventIn(BaseModel):
    type: str
    timestamp: datetime | None = None   # browser wall-clock time
    offset_ms: int | None = None        # ms since the recording started
    details: dict | None = None


class EventsIn(BaseModel):
    events: list[EventIn] = Field(max_length=500)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _get_interview(db: Session, session_id: int) -> Interview:
    interview = db.get(Interview, session_id)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    return interview


def _active_session(db: Session, session_id: int) -> InterviewSession:
    session = session_manager.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or already finished")
    return session


def _llm_http_error(exc: LLMError) -> HTTPException:
    """Model-layer failure -> an honest HTTP error (never a fake interviewer reply)."""
    headers = {"Retry-After": "5"} if exc.retryable else None
    return HTTPException(
        status_code=exc.http_status,
        detail=f"AI interviewer unavailable: {exc.message}",
        headers=headers,
    )


def _acquire(session: InterviewSession) -> None:
    """One turn at a time per interview (protects against double submissions)."""
    if not session.lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="The interviewer is still responding")


def _speak(text: str | None, finished: bool, **extra) -> dict:
    """Response body: the interviewer's line as AUDIO. Text is only included when debugging."""
    body: dict = {"status": "ok", "finished": finished, **extra}
    if text:
        try:
            wav = voice_service.synthesize(text)
        except Exception as exc:
            logger.exception("Voice synthesis failed")
            raise HTTPException(status_code=500, detail="Voice synthesis failed. Please try again.") from exc
        body["audio_base64"] = base64.b64encode(wav).decode("ascii")
        if settings.DEBUG_EXPOSE_TEXT:
            body["ai_text"] = text
    return body


def _finish_if_done(db: Session, session_id: int, session: InterviewSession,
                    background: BackgroundTasks | None = None) -> None:
    session_manager.persist(
        db,
        session_id,
        session,
        close=(
            ("ended_early" if session.end_reason in {"candidate_ended", "unresponsive"} else "finished")
            if session.finished
            else None
        ),
    )
    if session.finished and background is not None:
        background.add_task(evaluation_service.evaluate_in_background, session_id)


def _to_naive_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.utcnow()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


# ---------------------------------------------------------
# Interview flow
# ---------------------------------------------------------

@router.post("/start")
def start_session(data: StartRequest, db: Session = Depends(get_db)):
    candidate = db.get(Candidate, data.candidate_id)
    # Same answer for "no such candidate" and "wrong token": candidate ids must not be guessable/enumerable.
    if not candidate or not _same(candidate.invite_token, data.invite_token):
        raise HTTPException(status_code=403, detail="This interview link is not valid.")
    allowed, reason = candidate_service.can_start_interview(candidate)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)

    try:
        interview, session = session_manager.create_session(db, candidate)
    except LLMError as exc:
        logger.error("Could not prepare interview (LLM): %s | %s", exc.message, exc.detail)
        raise _llm_http_error(exc) from exc
    except Exception as exc:
        logger.exception("Could not prepare interview")
        raise HTTPException(status_code=500, detail="Could not prepare the interview. Please try again.") from exc

    if settings.live_mode:
        # Gemini Live speaks the opening itself, over the WebSocket. Nothing to generate or synthesise here.
        return {"status": "ok", "finished": False, "mode": "live", "session_id": interview.id,
                "candidate_id": candidate.id, "session_token": interview.access_token,
                "interviewer_name": session.cfg.interviewer_name,
                "live": {"input_rate": 16000, "output_rate": 24000}}

    try:
        text = session.start()
        body = _speak(
            text, False,
            session_id=interview.id,
            candidate_id=candidate.id,
            session_token=interview.access_token,
        )
    except HTTPException:
        raise
    except LLMError as exc:
        logger.error("AI interviewer failed to start: %s | %s", exc.message, exc.detail)
        session_manager.SESSIONS.pop(interview.id, None)
        interview.status = "failed"
        candidate_service.refund_attempt(db, candidate)
        db.commit()
        raise _llm_http_error(exc) from exc
    except Exception as exc:
        logger.exception("AI interviewer failed to start")
        session_manager.SESSIONS.pop(interview.id, None)
        interview.status = "failed"
        candidate_service.refund_attempt(db, candidate)
        db.commit()
        raise HTTPException(status_code=500, detail="The AI interviewer failed. Please try again.") from exc

    session_manager.persist(db, interview.id, session)
    return body


@router.post("/{session_id}/voice-answer", dependencies=[Depends(require_session_token)])
def voice_answer(
    session_id: int,
    background: BackgroundTasks,
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    session = _active_session(db, session_id)
    audio_bytes = audio.file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    suffix = Path(audio.filename or "").suffix.lower() or ".wav"
    logger.info("voice-answer: session=%s bytes=%d suffix=%s", session_id, len(audio_bytes), suffix)

    _acquire(session)
    try:
        # 1) Speech -> text (Whisper)
        try:
            candidate_text = voice_service.transcribe(audio_bytes, suffix=suffix).strip()
        except Exception as exc:
            logger.exception("Speech recognition failed")
            raise HTTPException(status_code=500, detail="Speech recognition failed. Please try again.") from exc

        # 2) Nothing intelligible -> the interviewer may ask the candidate to repeat
        if not candidate_text:
            return _handle_empty(db, session_id, session, silence_timeout=False, background=background)

        # 3) The model decides the next move
        try:
            ai_text = session.answer(candidate_text)
        except LLMError as exc:
            logger.error("AI interviewer failed: %s | %s", exc.message, exc.detail)
            raise _llm_http_error(exc) from exc
        except Exception as exc:
            logger.exception("AI interviewer failed")
            raise HTTPException(status_code=500, detail="The AI interviewer failed. Please try again.") from exc

        _finish_if_done(db, session_id, session, background)
        extra = {"candidate_text": candidate_text} if settings.DEBUG_EXPOSE_TEXT else {}

        # 4) Text -> speech (Piper)
        return _speak(ai_text, session.finished, **extra)
    finally:
        session.lock.release()


@router.post("/{session_id}/no-response", dependencies=[Depends(require_session_token)])
def no_response(session_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    """The candidate stayed silent for the configured time: the interviewer checks in."""
    session = _active_session(db, session_id)
    _acquire(session)
    try:
        return _handle_empty(db, session_id, session, silence_timeout=True, background=background)
    finally:
        session.lock.release()


def _handle_empty(db: Session, session_id: int, session: InterviewSession, *, silence_timeout: bool,
                  background: BackgroundTasks | None = None) -> dict:
    try:
        text, finished = session.register_empty(silence_timeout=silence_timeout)
    except LLMError as exc:
        logger.error("AI interviewer failed: %s | %s", exc.message, exc.detail)
        raise _llm_http_error(exc) from exc
    except Exception as exc:
        logger.exception("AI interviewer failed")
        raise HTTPException(status_code=500, detail="The AI interviewer failed. Please try again.") from exc

    if text is None:
        return {"status": "no_speech", "finished": False}

    _finish_if_done(db, session_id, session, background)
    return _speak(text, finished)


@router.post("/{session_id}/end", dependencies=[Depends(require_session_token)])
def end_session(session_id: int, background: BackgroundTasks, data: EndRequest | None = None,
                db: Session = Depends(get_db)):
    session = session_manager.get_session(db, session_id)
    if session:
        session.end((data.reason if data else None) or "candidate_ended")
        session_manager.persist(db, session_id, session, close="ended_early")
        background.add_task(evaluation_service.evaluate_in_background, session_id)
    return {"session_id": session_id, "finished": True}


# ---------------------------------------------------------
# Monitoring events (face present / missing / multiple / movement)
# ---------------------------------------------------------

@router.post("/{session_id}/events", dependencies=[Depends(require_session_token)])
def add_events(session_id: int, data: EventsIn, db: Session = Depends(get_db)):
    interview = _get_interview(db, session_id)
    allowed = settings.allowed_event_types

    stored = 0
    for item in data.events:
        if item.type.lower() not in allowed:
            continue  # unknown types are ignored, never inferred or stored
        db.add(
            InterviewEvent(
                interview_id=interview.id,
                event_type=item.type.lower(),
                occurred_at=_to_naive_utc(item.timestamp),
                offset_ms=item.offset_ms,
                details=item.details,
            )
        )
        stored += 1
    db.commit()
    return {"stored": stored, "ignored": len(data.events) - stored}


class ViolationIn(BaseModel):
    type: str = "tab_switch"
    offset_ms: int | None = None


@router.post("/{session_id}/violation", dependencies=[Depends(require_session_token)])
def add_violation(session_id: int, data: ViolationIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    """The browser tab / window lost focus. Server-side rule: warning 1, warning 2, the next one ends the interview."""
    _get_interview(db, session_id)
    if data.type != "tab_switch":
        raise HTTPException(status_code=400, detail="Unknown violation type")
    outcome = proctoring_service.record_tab_switch(db, session_id, data.offset_ms)
    if outcome["ended"] and outcome["count"] > outcome["limit"] and outcome["count"] == outcome["limit"] + 1:
        interview = db.get(Interview, session_id)
        if (interview.mode or "voice") == "chat":
            background.add_task(chat_service.notify_completion_in_background, session_id)
        elif not settings.live_mode:    # live interviews are closed (and evaluated) by their own socket
            background.add_task(evaluation_service.evaluate_in_background, session_id)
    return outcome


# ---------------------------------------------------------
# Recording (camera + microphone of the whole interview)
# ---------------------------------------------------------

@router.post("/{session_id}/video", dependencies=[Depends(require_session_token)])
def upload_video(
    session_id: int,
    background: BackgroundTasks,
    video: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    interview = _get_interview(db, session_id)

    if not video.filename:
        raise HTTPException(status_code=400, detail="Video filename is missing")

    suffix = Path(video.filename).suffix.lower()
    allowed = settings.allowed_video_extensions
    if suffix not in allowed:
        raise HTTPException(
            status_code=400, detail=f"Allowed video formats: {', '.join(sorted(allowed))}"
        )

    folder = Path(settings.UPLOAD_DIR) / settings.VIDEO_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"interview_{session_id}{suffix}"
    max_bytes = settings.MAX_VIDEO_MB * 1024 * 1024

    written, too_big = 0, False
    with path.open("wb") as out:
        while True:
            chunk = video.file.read(CHUNK_SIZE)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                too_big = True
                break
            out.write(chunk)

    if too_big:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="Video file is too large")
    if written == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Video file is empty")

    try:
        stored = storage.store_file(
            path, f"videos/{path.name}", content_type=video.content_type or "video/webm"
        )
    except Exception as exc:
        logger.exception("Could not store recording")
        raise HTTPException(status_code=500, detail="Could not store the recording") from exc

    interview.video_path = stored
    interview.video_size_bytes = written
    interview.video_uploaded_at = datetime.utcnow()
    db.commit()
    if settings.SUMMARY_AUTO:   # the recording is the last thing the browser sends: everything is in now
        background.add_task(summary_service.generate_summary_in_background, session_id)
    return {"session_id": session_id, "size_bytes": written}


class VideoUrlRequest(BaseModel):
    content_type: str = "video/webm"


_VIDEO_TYPES = {"video/webm": ".webm", "video/mp4": ".mp4"}


@router.post("/{session_id}/video/upload-url", dependencies=[Depends(require_session_token)])
def video_upload_url(session_id: int, data: VideoUrlRequest, db: Session = Depends(get_db)):
    """
    Cloud Run limits a request body to 32 MiB, and interview recordings are bigger than that. In production
    (STORAGE_BACKEND=gcs) the browser therefore uploads the recording straight to the private bucket with a
    short-lived signed URL, then calls /video/complete. Locally it just uses the plain /video endpoint.
    """
    _get_interview(db, session_id)
    if not storage.is_gcs():
        return {"mode": "direct"}
    base_type = (data.content_type or "").split(";")[0].strip().lower()
    ext = _VIDEO_TYPES.get(base_type)
    if not ext or ext not in settings.allowed_video_extensions:
        raise HTTPException(status_code=400, detail="Unsupported recording format.")
    max_bytes = settings.MAX_VIDEO_MB * 1024 * 1024
    uri = storage.upload_target(f"videos/interview_{session_id}{ext}")
    try:
        url = storage.signed_url(uri, method="PUT", content_type=base_type,
                                 headers={"x-goog-content-length-range": f"1,{max_bytes}"})
    except Exception as exc:
        logger.exception("Could not create a signed upload URL")
        raise HTTPException(status_code=500, detail="Could not prepare the recording upload.") from exc
    return {"mode": "gcs", "url": url, "content_type": base_type,
            "headers": {"x-goog-content-length-range": f"1,{max_bytes}"}, "max_bytes": max_bytes}


@router.post("/{session_id}/video/complete", dependencies=[Depends(require_session_token)])
def video_upload_complete(session_id: int, data: VideoUrlRequest, background: BackgroundTasks,
                          db: Session = Depends(get_db)):
    """The browser finished its direct upload: verify the object really exists, then record the reference."""
    interview = _get_interview(db, session_id)
    if not storage.is_gcs():
        raise HTTPException(status_code=400, detail="Direct upload is not enabled.")
    ext = _VIDEO_TYPES.get((data.content_type or "").split(";")[0].strip().lower())
    if not ext:
        raise HTTPException(status_code=400, detail="Unsupported recording format.")
    uri = storage.upload_target(f"videos/interview_{session_id}{ext}")   # derived server-side, never from the client
    try:
        size = storage.object_size(uri)
    except Exception as exc:
        logger.exception("Could not verify the uploaded recording")
        raise HTTPException(status_code=500, detail="Could not verify the recording.") from exc
    if not size:
        raise HTTPException(status_code=400, detail="The recording was not found. Please upload it again.")
    interview.video_path = uri
    interview.video_size_bytes = size
    interview.video_uploaded_at = datetime.utcnow()
    db.commit()
    if settings.SUMMARY_AUTO:
        background.add_task(summary_service.generate_summary_in_background, session_id)
    return {"session_id": session_id, "size_bytes": size}


# ---------------------------------------------------------
# Result
# ---------------------------------------------------------

@router.get("/{session_id}/result")
def result(session_id: int, role: str = Depends(caller_role), db: Session = Depends(get_db)):
    interview = _get_interview(db, session_id)
    if role != "admin":      # the candidate only sees a short feedback view of their own result
        return {"id": interview.id, "status": interview.status, "end_reason": interview.end_reason,
                "summary": chat_service.candidate_view(interview, interview.summary)}
    return {
        "id": interview.id,
        "candidate_id": interview.candidate_id,
        "status": interview.status,
        "end_reason": interview.end_reason,
        "mode": interview.mode or "voice",
        "tab_switch_count": interview.tab_switch_count or 0,
        "started_at": interview.started_at,
        "ended_at": interview.ended_at,
        "settings": interview.settings_snapshot,
        "transcript": interview.transcript or [],
        "chat_work": chat_service.work_for_admin(interview) if interview.chat_tasks else None,
        "has_recording": bool(interview.video_path),
        "video_size_bytes": interview.video_size_bytes,
        "summary": interview.summary,
        "events": [
            {
                "type": e.event_type,
                "timestamp": e.occurred_at,
                "offset_ms": e.offset_ms,
                "details": e.details,
            }
            for e in interview.events
        ],
    }


@router.post("/{session_id}/summary")
def make_summary(session_id: int, force: bool = False, role: str = Depends(caller_role),
                 db: Session = Depends(get_db)):
    """Factual summary of the interview + the observable recording events. `?force=true` (admin only) regenerates it."""
    interview = _get_interview(db, session_id)
    try:
        summary = summary_service.generate_summary(db, session_id, force=force and role == "admin")
    except LLMError as exc:        # not expected (build_summary catches them) - kept for safety
        raise _llm_http_error(exc) from exc
    if role != "admin":
        db.refresh(interview)
        return chat_service.candidate_view(interview, summary)
    return summary
