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


def build_system_prompt(
    *,
    interviewer_name: str,
    tone: str,
    interview_type: str,
    candidate_name: str,
    resume_profile: dict | None,
    resume_text: str | None,
) -> str:
    """resume_text is None when the resume is too long to include verbatim."""
    prompts = load_prompts()
    if resume_text:
        block = f'\nFULL RESUME TEXT:\n"""\n{resume_text}\n"""\n'
    else:
        block = ""
    return render(
        prompts["system_template"],
        interviewer_name=interviewer_name,
        tone=tone,
        interview_type=interview_type,
        candidate_name=candidate_name,
        bpo_context=format_bpo_context(load_bpo_context()),
        resume_profile=format_profile(resume_profile),
        resume_text_block=block,
    )
