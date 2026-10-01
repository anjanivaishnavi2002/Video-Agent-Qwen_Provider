"""Loads the consent form (data file) and fills in the organisation's details."""
from functools import lru_cache
from string import Template

import yaml

from app.config import settings


@lru_cache(maxsize=1)
def _raw() -> dict:
    with open(settings.CONSENT_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_consent() -> dict:
    values = {
        "organization": settings.ORGANIZATION_NAME,
        "retention_days": settings.DATA_RETENTION_DAYS,
        "contact": settings.CONSENT_CONTACT_EMAIL or "the hiring team",
        "interviewer": settings.INTERVIEWER_NAME,
        "minutes": settings.INTERVIEW_DURATION_MINUTES,
    }

    def fill(text: str) -> str:
        return Template(str(text)).safe_substitute(values)

    raw = _raw()
    return {
        "version": str(raw["version"]),
        "title": fill(raw["title"]),
        "intro": fill(raw.get("intro", "")),
        "sections": [
            {
                "heading": fill(s["heading"]),
                "body": [fill(t) for t in s.get("body", [])],
                "bullets": [fill(t) for t in s.get("bullets", [])],
            }
            for s in raw.get("sections", [])
        ],
        "statements": [fill(t) for t in raw.get("statements", [])],
    }
