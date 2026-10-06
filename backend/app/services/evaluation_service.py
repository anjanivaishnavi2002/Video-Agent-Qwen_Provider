"""Final AI evaluation of a finished interview (Gemini, structured output). Decision support only."""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Candidate, Evaluation, Interview, Job
from app.prompts.interviewer import format_job, format_profile, load_prompts, render
from app.providers.factory import get_llm
from app.providers.llm_errors import LLMError
from app.services import notification_client
from app.services.summary_service import format_transcript

logger = logging.getLogger(__name__)

RECOMMENDATIONS = ["strong_fit", "fit", "borderline", "not_a_fit", "insufficient_data"]

EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "number"},
        "recommendation": {"type": "string", "enum": RECOMMENDATIONS},
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "score": {"type": "number"}, "evidence": {"type": "string"}},
                "required": ["name", "score", "evidence"],
            },
        },
    },
    "required": ["overall_score", "recommendation", "summary", "strengths", "concerns", "criteria"],
}

MIN_CANDIDATE_ANSWERS = 2


def _clamp(value, low=0.0, high=100.0) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


def clean_evaluation(raw: dict) -> dict:
    """Normalise the model's JSON (types, ranges, lengths) before it is stored."""
    recommendation = str(raw.get("recommendation", "")).strip()
    if recommendation not in RECOMMENDATIONS:
        recommendation = "borderline"
    criteria = []
    for item in (raw.get("criteria") or [])[:10]:
        if isinstance(item, dict) and str(item.get("name", "")).strip():
            criteria.append({"name": str(item["name"]).strip()[:80], "score": _clamp(item.get("score")),
                             "evidence": str(item.get("evidence", "")).strip()[:600]})
    as_list = lambda v: [str(x).strip()[:400] for x in (v or []) if str(x).strip()][:8]  # noqa: E731
    return {
        "overall_score": _clamp(raw.get("overall_score")),
        "recommendation": recommendation,
        "summary": str(raw.get("summary", "")).strip()[:2000],
        "strengths": as_list(raw.get("strengths")),
        "concerns": as_list(raw.get("concerns")),
        "criteria": criteria,
    }


def _answers(interview: Interview) -> int:
    return sum(1 for t in (interview.transcript or []) if t.get("role") == "candidate")


def evaluate_interview(db: Session, interview_id: int, *, force: bool = False) -> Evaluation | None:
    interview = db.get(Interview, interview_id)
    if not interview:
        return None
    if interview.evaluation and not force:
        return interview.evaluation
    candidate = db.get(Candidate, interview.candidate_id)
    job_id = interview.job_id or (candidate.job_id if candidate else None)
    job = db.get(Job, job_id) if job_id else None

    if _answers(interview) < MIN_CANDIDATE_ANSWERS:
        data = {"overall_score": None, "recommendation": "insufficient_data", "strengths": [], "concerns": [],
                "criteria": [], "summary": "The candidate gave too few answers for an evaluation."}
    else:
        prompts = load_prompts()
        transcript = (interview.transcript or [])
        text = format_transcript(transcript)[: settings.SUMMARY_MAX_TRANSCRIPT_CHARS]
        job_ctx = {"title": job.title, "description": job.description, "location": job.location,
                   "skills": job.required_skills or []} if job else None
        user = render(prompts["evaluation_user"], job_block=format_job(job_ctx),
                      resume_profile=format_profile(candidate.resume_profile if candidate else None),
                      turns=_answers(interview), end_reason=interview.end_reason or "unknown", transcript=text)
        messages = [{"role": "system", "content": prompts["evaluation_system"]}, {"role": "user", "content": user}]
        data = clean_evaluation(get_llm().chat_json(messages, EVALUATION_SCHEMA, temperature=settings.SUMMARY_TEMPERATURE))

    evaluation = interview.evaluation or Evaluation(interview_id=interview.id, candidate_id=interview.candidate_id)
    evaluation.overall_score = data["overall_score"]
    evaluation.recommendation = data["recommendation"]
    evaluation.summary = data["summary"]
    evaluation.strengths, evaluation.concerns, evaluation.criteria = data["strengths"], data["concerns"], data["criteria"]
    evaluation.model = settings.active_model
    evaluation.updated_at = datetime.utcnow()
    db.add(evaluation)
    if candidate and data["overall_score"] is not None:
        candidate.interview_score = data["overall_score"]
        candidate.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(evaluation)
    return evaluation


def evaluate_in_background(interview_id: int, *, notify: bool = True) -> None:
    """Runs after the interview closed (own DB session). Never raises: failures are logged and can be retried by an admin."""
    db = SessionLocal()
    try:
        evaluation = evaluate_interview(db, interview_id)
        logger.info("Interview %s evaluated (%s)", interview_id,
                    evaluation.recommendation if evaluation else "n/a")
    except LLMError as exc:
        logger.error("Evaluation of interview %s failed: %s | %s", interview_id, exc.message, exc.detail)
    except Exception:
        logger.exception("Evaluation of interview %s failed", interview_id)
    finally:
        try:
            if notify:
                interview = db.get(Interview, interview_id)
                candidate = db.get(Candidate, interview.candidate_id) if interview else None
                if candidate:
                    notification_client.notify_candidate(db, candidate, "completion", interview=interview)
        except Exception:
            logger.exception("Completion notification for interview %s failed", interview_id)
        db.close()
