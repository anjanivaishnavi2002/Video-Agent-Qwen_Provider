"""
Post-interview report: a factual summary of what was said, plus a deterministic report of the
observable recording events (face missing / returned / multiple faces / head movement).

Deliberately NOT included: emotion, confidence, honesty or personality inference, scores, or hiring
recommendations. The summary comes from the transcript only; the event report is plain counting.
"""
import logging
from datetime import datetime

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Candidate, Interview
from app.prompts.interviewer import load_prompts, render
from app.providers import gemini_provider
from app.providers.llm_errors import LLMError

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "topics_discussed": {"type": "array", "items": {"type": "string"}},
        "stated_experience": {"type": "array", "items": {"type": "string"}},
        "unanswered_or_unclear": {"type": "array", "items": {"type": "string"}},
        "follow_up_topics": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "topics_discussed", "stated_experience"],
}

EVENT_NOTE = (
    "These are observable camera events only (a face was or was not in view, how many faces, head movement). "
    "They say nothing about emotion, confidence or honesty and should not be read as such."
)


def get_llm():
    return gemini_provider.get_llm()


def format_transcript(transcript: list[dict]) -> str:
    lines = []
    for turn in transcript:
        who = "Interviewer" if turn.get("role") == "assistant" else "Candidate"
        text = str(turn.get("text", "")).strip()
        if text:
            lines.append(f"{who}: {text}")
    text = "\n".join(lines)
    limit = settings.SUMMARY_MAX_TRANSCRIPT_CHARS
    return text if len(text) <= limit else text[:limit] + "\n[transcript shortened for length]"


def summarize_events(events) -> dict:
    """Plain counting over the stored events - no model involved."""
    counts: dict[str, int] = {}
    missing_ms = 0
    missing_since = None
    timeline = []
    for event in sorted(events, key=lambda e: (e.offset_ms is None, e.offset_ms or 0, e.occurred_at)):
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
        if len(timeline) < 100:
            timeline.append({"offset_ms": event.offset_ms, "type": event.event_type, "details": event.details})
        if event.offset_ms is None:
            continue
        if event.event_type == "face_missing" and missing_since is None:
            missing_since = event.offset_ms
        elif event.event_type == "face_returned" and missing_since is not None:
            missing_ms += max(0, event.offset_ms - missing_since)
            missing_since = None
    return {
        "counts": counts,
        "face_missing_seconds": round(missing_ms / 1000, 1),
        "face_missing_at_end": missing_since is not None,
        "timeline": timeline,
        "note": EVENT_NOTE,
    }


def build_summary(interview: Interview, candidate_name: str) -> dict:
    """Build the report dict for one interview (calls the model once for the text summary)."""
    transcript = interview.transcript or []
    answers = sum(1 for t in transcript if t.get("role") == "candidate")
    report = {
        "status": "ready",
        "generated_at": datetime.utcnow().isoformat(),
        "recording": {
            "uploaded": bool(interview.video_path),
            "size_bytes": interview.video_size_bytes,
            "events": summarize_events(interview.events or []),
        },
    }
    if answers == 0:
        report["status"] = "skipped"
        report["reason"] = "The candidate gave no answers, so there is nothing to summarise."
        return report

    started, ended = interview.started_at, interview.ended_at
    minutes = round(((ended or datetime.utcnow()) - started).total_seconds() / 60) if started else "?"
    prompts = load_prompts()
    try:
        summary = get_llm().chat_json(
            [
                {"role": "system", "content": prompts["summary_system"]},
                {"role": "user", "content": render(
                    prompts["summary_user"], candidate_name=candidate_name, minutes=minutes,
                    answers=answers, transcript=format_transcript(transcript))},
            ],
            SUMMARY_SCHEMA,
            temperature=settings.SUMMARY_TEMPERATURE,
        )
    except LLMError as exc:
        logger.error("Summary failed: %s | %s", exc.message, exc.detail)
        report["status"] = "failed"
        report["error"] = exc.message
        return report

    report["model"] = settings.active_model
    report["interview"] = {k: summary.get(k, []) for k in SUMMARY_SCHEMA["properties"]}
    return report


def generate_summary(db, interview_id: int, *, force: bool = False) -> dict | None:
    """Return the stored summary, generating (or regenerating with force) it when needed."""
    interview = db.get(Interview, interview_id)
    if not interview:
        return None
    if interview.summary and interview.summary.get("status") in ("ready", "skipped") and not force:
        return interview.summary
    candidate = db.get(Candidate, interview.candidate_id)
    interview.summary = build_summary(interview, candidate.name if candidate else "the candidate")
    db.commit()
    return interview.summary


def generate_summary_in_background(interview_id: int) -> None:
    """Runs after the response has been sent (FastAPI background task) with its own DB session."""
    db = SessionLocal()
    try:
        generate_summary(db, interview_id)
    except Exception:
        logger.exception("Background summary failed for interview %s", interview_id)
    finally:
        db.close()
