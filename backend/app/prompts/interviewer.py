"""Loads prompt/context data files and renders them. Contains no interview content itself."""
import json
from functools import lru_cache
from string import Template

import yaml

from app.config import settings


@lru_cache(maxsize=1)
def load_prompts() -> dict:
    with open(settings.PROMPT_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_bpo_context() -> dict:
    with open(settings.BPO_CONTEXT_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def render(template: str, **values) -> str:
    return Template(template).safe_substitute(**{k: str(v) for k, v in values.items()})


def format_bpo_context(ctx: dict) -> str:
    lines = []
    if ctx.get("domain"):
        lines.append(f"Domain: {' '.join(str(ctx['domain']).split())}")
    if ctx.get("assessment_areas"):
        lines.append("Areas an interviewer in this domain listens for:")
        lines += [f"- {a}" for a in ctx["assessment_areas"]]
    if ctx.get("guidance"):
        lines.append("Guidance:")
        lines += [f"- {g}" for g in ctx["guidance"]]
    return "\n".join(lines)


def format_profile(profile: dict | None) -> str:
    if not profile:
        return "(No structured profile available - rely on the resume text below.)"
    return json.dumps(profile, ensure_ascii=False, indent=1)


def format_job(job: dict | None) -> str:
    if not job or not (job.get("description") or job.get("title")):
        return "(No specific job was provided - interview for general suitability for the domain above.)"
    lines = [f"Job title: {job.get('title') or 'not specified'}"]
    if job.get("location"):
        lines.append(f"Location: {job['location']}")
    if job.get("skills"):
        lines.append("Required skills: " + ", ".join(str(x) for x in job["skills"]))
    lines.append('Job description:\n"""\n' + str(job.get("description") or "").strip() + '\n"""')
    return "\n".join(lines)


def format_candidate_info(info: dict | None) -> str:
    """Only what helps the interview (no e-mail / phone: the interviewer never needs contact details)."""
    parts = []
    if info:
        if info.get("location"):
            parts.append(f"Location: {info['location']}")
        if info.get("experience_years") is not None:
            parts.append(f"Self-reported experience: {info['experience_years']} years")
        if info.get("skills"):
            parts.append("Self-reported skills: " + ", ".join(str(x) for x in info["skills"]))
    return "\n".join(parts) if parts else "(none provided)"


def build_system_prompt(
    *,
    interviewer_name: str,
    tone: str,
    interview_type: str,
    candidate_name: str,
    resume_profile: dict | None,
    resume_text: str | None,
    job: dict | None = None,
    candidate_info: dict | None = None,
) -> str:
    """resume_text is None when the resume is too long to include verbatim. `job` = {title, description, skills}."""
    prompts = load_prompts()
    if resume_text:
        block = f'\nFULL RESUME TEXT:\n"""\n{resume_text}\n"""\n'
    else:
        block = ""
    return render(
        prompts["system_template"],
        job_block=format_job(job),
        candidate_info=format_candidate_info(candidate_info),
        interviewer_name=interviewer_name,
        tone=tone,
        interview_type=interview_type,
        candidate_name=candidate_name,
        bpo_context=format_bpo_context(load_bpo_context()),
        resume_profile=format_profile(resume_profile),
        resume_text_block=block,
    )
