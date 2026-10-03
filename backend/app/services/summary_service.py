"""
Post-interview report: a factual summary of what was said, plus a deterministic report of the
observable recording events (face missing / returned / multiple faces / head movement).

Scorecard: criteria are chosen by the model from the role discussed in the interview (nothing is hard-coded),
each scored 1-5 with evidence from the transcript. It rates ONLY the content of what the candidate said - never
voice, accent, appearance, emotion, confidence, honesty or personality - and it is advisory: a person decides.
There is no hire / reject recommendation. The event report is plain counting.
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

SCORECARD_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "score": {"type": "integer"},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "score", "evidence"],
            },
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "areas_to_probe": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["criteria"],
}

SCORECARD_NOTE = (
    "Advisory only. Scores rate the content of the candidate's spoken answers against criteria drawn from the role "
    "discussed in the interview. They do not rate voice, accent, appearance, emotion, confidence or honesty, and "
    "they are not a hiring decision. A person should review the transcript."
)
MIN_ANSWERS_FOR_SCORECARD = 3


def clean_scorecard(raw: dict | None) -> dict | None:
    """Validate the model's scorecard: clamp scores to 1-5, drop empty rows, compute the overall score here."""
    if not isinstance(raw, dict):
        return None
    criteria = []
    for item in (raw.get("criteria") or [])[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        try:
            score = int(round(float(item.get("score"))))
        except (TypeError, ValueError):
            continue
        if not name or not evidence:      # a score without evidence from the transcript is not shown
            continue
        criteria.append({"name": name[:80], "score": max(1, min(5, score)), "evidence": evidence[:400]})
    if not criteria:
        return None
    overall = round(sum(c["score"] for c in criteria) / len(criteria), 1)
    return {
        "criteria": criteria,
        "overall_score": overall,
        "scale": "1 (no relevant evidence) to 5 (specific, relevant and well explained)",
        "strengths": [str(x)[:300] for x in (raw.get("strengths") or [])[:6] if str(x).strip()],
        "areas_to_probe": [str(x)[:300] for x in (raw.get("areas_to_probe") or [])[:6] if str(x).strip()],
        "note": SCORECARD_NOTE,
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

    if answers < MIN_ANSWERS_FOR_SCORECARD:
        report["scorecard"] = {
            "status": "skipped",
            "reason": f"Only {answers} candidate answer(s): not enough to score fairly.",
        }
        return report
    try:
        raw = get_llm().chat_json(
            [
                {"role": "system", "content": prompts["scorecard_system"]},
                {"role": "user", "content": render(
                    prompts["scorecard_user"], candidate_name=candidate_name, minutes=minutes,
                    answers=answers, transcript=format_transcript(transcript))},
            ],
            SCORECARD_SCHEMA,
            temperature=settings.SUMMARY_TEMPERATURE,
        )
        card = clean_scorecard(raw)
        report["scorecard"] = (
            {"status": "ready", **card} if card
            else {"status": "failed", "error": "The model returned no usable scores."}
        )
    except LLMError as exc:
        logger.error("Scorecard failed: %s | %s", exc.message, exc.detail)
        report["scorecard"] = {"status": "failed", "error": exc.message}
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
