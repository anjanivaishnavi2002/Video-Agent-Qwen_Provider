"""Resume storage, text extraction and structured analysis."""
import logging
import re
import uuid
from pathlib import Path

from app.config import settings
from app.prompts.interviewer import load_prompts, render
from app.providers.ollama_provider import OllamaProvider
from app.providers.llm_errors import LLMResponseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Upload + text extraction
# ---------------------------------------------------------------------------

def validate_upload(filename: str, size_bytes: int) -> str:
    """Returns the lower-case extension, or raises ValueError with a user-facing message."""
    ext = Path(filename or "").suffix.lower()
    allowed = settings.allowed_resume_extensions
    if ext not in allowed:
        raise ValueError(f"Allowed resume formats: {', '.join(sorted(allowed))}")
    if size_bytes == 0:
        raise ValueError("The uploaded file is empty.")
    if size_bytes > settings.MAX_RESUME_MB * 1024 * 1024:
        raise ValueError(f"Resume is too large (limit {settings.MAX_RESUME_MB} MB).")
    return ext


def save_upload(filename: str, data: bytes) -> str:
    ext = validate_upload(filename, len(data))
    folder = Path(settings.UPLOAD_DIR) / settings.RESUME_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    return str(path)


def _clean(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(path: str) -> str:
    """Extract the FULL resume text (no truncation except the storage safety cap)."""
    ext = Path(path).suffix.lower()

    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif ext == ".docx":
        from docx import Document

        doc = Document(path)
        parts = [p.text for p in doc.paragraphs]
        # Many resumes keep experience/skills inside tables.
        for table in doc.tables:
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    if cell.text not in cells:  # merged cells repeat
                        cells.append(cell.text)
                parts.append(" | ".join(c.strip() for c in cells if c.strip()))
        text = "\n".join(parts)
    else:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")

    return _clean(text)[: settings.RESUME_STORE_MAX_CHARS]


# ---------------------------------------------------------------------------
# Resume analysis  (facts only, grounded in the text)
# ---------------------------------------------------------------------------

_STR_LIST = {"type": "array", "items": {"type": "string"}}

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "total_experience": {"type": "string"},
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "duration": {"type": "string"},
                    "responsibilities": _STR_LIST,
                },
                "required": ["title"],
            },
        },
        "skills": _STR_LIST,
        "tools_and_systems": _STR_LIST,
        "projects": _STR_LIST,
        "education": _STR_LIST,
        "languages": _STR_LIST,
        "certifications": _STR_LIST,
    },
    "required": ["roles", "skills"],
}

_LIST_FIELDS = ["skills", "tools_and_systems", "projects", "education", "languages", "certifications"]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+#. ]+", " ", (text or "").lower()).strip()


def _words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if len(w) > 2}


def _supported(item: str, norm_text: str, text_words: set[str]) -> bool:
    """True if the item is actually backed by the resume text (guards against invention)."""
    n = _norm(item)
    if not n:
        return False
    if n in " ".join(norm_text.split()):
        return True
    words = _words(item)
    if not words:
        return False
    return len(words & text_words) / len(words) >= 0.6


def ground_profile(profile: dict, resume_text: str) -> dict:
    """Drop every extracted item that the resume text does not support."""
    norm_text = _norm(resume_text)
    text_words = _words(resume_text)
    check = lambda s: _supported(str(s), norm_text, text_words)  # noqa: E731

    out: dict = {}
    for key in ("summary", "total_experience"):
        val = profile.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()

    roles = []
    for role in profile.get("roles") or []:
        if not isinstance(role, dict):
            continue
        title, company = role.get("title") or "", role.get("company") or ""
        if not (check(title) or (company and check(company))):
            continue
        roles.append(
            {
                "title": title if check(title) else "",
                "company": company if company and check(company) else "",
                "duration": role.get("duration") or "",
                "responsibilities": [r for r in (role.get("responsibilities") or []) if check(r)],
            }
        )
    out["roles"] = roles

    for key in _LIST_FIELDS:
        out[key] = [v for v in (profile.get(key) or []) if isinstance(v, str) and check(v)]
    return out


def _merge_profiles(profiles: list[dict]) -> dict:
    merged: dict = {"summary": "", "total_experience": "", "roles": []}
    for key in _LIST_FIELDS:
        merged[key] = []

    seen_roles: set[tuple] = set()
    for p in profiles:
        if p.get("summary") and not merged["summary"]:
            merged["summary"] = p["summary"]
        if p.get("total_experience") and not merged["total_experience"]:
            merged["total_experience"] = p["total_experience"]
        for r in p.get("roles", []):
            k = (_norm(r.get("title", "")), _norm(r.get("company", "")))
            if k not in seen_roles:
                seen_roles.add(k)
                merged["roles"].append(r)
        for key in _LIST_FIELDS:
            have = {_norm(x) for x in merged[key]}
            for x in p.get(key, []):
                if _norm(x) not in have:
                    merged[key].append(x)
                    have.add(_norm(x))
    return {k: v for k, v in merged.items() if v}


def _split_chunks(text: str, size: int) -> list[str]:
    """Split on paragraph boundaries into chunks of roughly `size` characters."""
    chunks, current = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > size:  # a single huge paragraph
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:size])
            para = para[size:]
        if len(current) + len(para) + 2 > size and current:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def analyze_resume(text: str, llm: OllamaProvider) -> dict | None:
    """
    Build a structured, grounded profile of the resume.

    Short resumes: one pass. Long resumes: every chunk is analysed and the
    profiles are merged, so nothing is dropped. Returns None on failure (the
    interview then falls back to the raw text).
    """
    prompts = load_prompts()
    chunks = (
        [text]
        if len(text) <= settings.RESUME_PROMPT_MAX_CHARS
        else _split_chunks(text, settings.RESUME_CHUNK_CHARS)
    )

    profiles = []
    for i, chunk in enumerate(chunks, 1):
        note = (
            f" This is part {i} of {len(chunks)} of a long resume; extract only what appears in this part."
            if len(chunks) > 1
            else ""
        )
        try:
            raw = llm.chat_json(
                [
                    {"role": "system", "content": prompts["resume_analysis_system"]},
                    {
                        "role": "user",
                        "content": render(prompts["resume_analysis_user"], text=chunk, chunk_note=note),
                    },
                ],
                PROFILE_SCHEMA,
                temperature=settings.RESUME_ANALYSIS_TEMPERATURE,
            )
        except LLMResponseError:
            # The model answered but not usably: skip this chunk (the interview then relies on the
            # raw resume text). Provider/auth/network errors are NOT swallowed - they propagate.
            logger.exception("Resume analysis gave an unusable answer on chunk %d/%d", i, len(chunks))
            raw = None
        if raw:
            profiles.append(ground_profile(raw, chunk))

    if not profiles:
        return None
    return profiles[0] if len(profiles) == 1 else _merge_profiles(profiles)


def resume_text_for_prompt(text: str) -> str | None:
    """Full text when it fits the prompt budget; None when only the profile should be used."""
    return text if len(text) <= settings.RESUME_PROMPT_MAX_CHARS else None
