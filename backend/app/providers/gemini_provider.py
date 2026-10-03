"""
Gemini access for the interview app: ONE provider, ONE SDK (google-genai), two back ends.

    LLM_PROVIDER=gemini   Gemini Developer API, authenticated with GEMINI_API_KEY   (local development)
    LLM_PROVIDER=vertex   Vertex AI Gemini, authenticated with Application Default Credentials,
                          i.e. the Compute Engine VM's service account - no API key  (production)

The rest of the app only sees `get_llm()` -> `.chat()` / `.chat_json()` and the typed
errors in `llm_errors`. Structured output uses Gemini's native JSON-schema mode, so the
interviewer's reply is JSON by construction instead of being parsed out of free text.
Failures are raised, never replaced by invented answers.
"""
import json
import logging
import re
import threading
import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.config import settings
from app.providers.llm_errors import (
    LLMAuthError,
    LLMConfigError,
    LLMConnectionError,
    LLMError,
    LLMModelNotFoundError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)

try:  # google-auth is installed with google-genai
    from google.auth import exceptions as google_auth_exceptions
except Exception:  # pragma: no cover
    google_auth_exceptions = None

logger = logging.getLogger(__name__)

# finish reasons that mean "the model produced no usable answer"
_BLOCKED_REASONS = {
    "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION", "IMAGE_SAFETY",
}


def build_client() -> genai.Client:
    """
    One place that creates the Google GenAI client for BOTH the text model and the Live model.

        LLM_PROVIDER=gemini -> Gemini Developer API with GEMINI_API_KEY (local development)
        LLM_PROVIDER=vertex -> Vertex AI with Application Default Credentials (the VM's service account)
    """
    backend = str(settings.LLM_PROVIDER).strip().lower()
    http_options = types.HttpOptions(timeout=int(settings.LLM_TIMEOUT_SECONDS * 1000))
    try:
        if backend == "gemini":
            if not settings.GEMINI_API_KEY:
                raise LLMConfigError(
                    "GEMINI_API_KEY is not set. Put it in backend/.env for local development "
                    "(or use LLM_PROVIDER=vertex on GCP)."
                )
            return genai.Client(vertexai=False, api_key=settings.GEMINI_API_KEY, http_options=http_options)
        if backend == "vertex":
            if not settings.VERTEX_PROJECT_ID:
                raise LLMConfigError(
                    "VERTEX_PROJECT_ID is not set (the Google Cloud project that has Vertex AI enabled)."
                )
            return genai.Client(
                vertexai=True,
                project=settings.VERTEX_PROJECT_ID,
                location=settings.VERTEX_LOCATION,
                http_options=http_options,
            )
        raise LLMConfigError(
            f"LLM_PROVIDER must be 'gemini' (API key) or 'vertex' (Vertex AI), not '{settings.LLM_PROVIDER}'."
        )
    except LLMError:
        raise
    except Exception as exc:
        raise _translate(exc) from exc


def translate_error(exc: Exception) -> LLMError:
    """Public wrapper: any exception -> the typed LLMError the API reports."""
    return exc if isinstance(exc, LLMError) else _translate(exc)



class GeminiProvider:
    def __init__(self):
        self.backend = str(settings.LLM_PROVIDER).strip().lower()
        self.model = settings.active_model
        if self.backend not in ("gemini", "vertex"):
            raise LLMConfigError(
                f"LLM_PROVIDER must be 'gemini' (API key) or 'vertex' (Vertex AI), not '{settings.LLM_PROVIDER}'."
            )
        if not self.model:
            raise LLMConfigError("No model configured: set GEMINI_MODEL / VERTEX_MODEL (or LLM_MODEL).")

        self._client = build_client()

        logger.info(
            "LLM ready: backend=%s model=%s%s thinking=%s",
            self.backend, self.model,
            f" project={settings.VERTEX_PROJECT_ID} location={settings.VERTEX_LOCATION}"
            if self.backend == "vertex" else "",
            settings.LLM_THINKING_LEVEL,
        )

    # ------------------------------------------------------------------
    # Public API (same shape the interview engine always used)
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        temperature: float | None = None,
    ) -> str:
        """Return the model's raw reply text (JSON text when a schema is given)."""
        return self._run(messages, schema, temperature, parse=False)

    def chat_json(self, messages: list[dict], schema: dict, **kwargs) -> dict:
        """Return the reply as a validated dict. Raises LLMResponseError if it is not usable."""
        return self._run(messages, schema, kwargs.get("temperature"), parse=True)

    def ping(self) -> dict:
        """Tiny live call used by /health?deep=true to prove credentials, model and network work."""
        started = time.time()
        text = self._run(
            [{"role": "user", "content": "Reply with the single word: OK"}],
            None, None, parse=False, max_tokens=256,
        )
        return {"ok": True, "latency_ms": int((time.time() - started) * 1000), "reply": text[:20]}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _models(self) -> list[str]:
        fallback = (settings.LLM_FALLBACK_MODEL or "").strip()
        return [self.model] + ([fallback] if fallback and fallback != self.model else [])

    def _run(self, messages, schema, temperature, *, parse: bool, max_tokens: int | None = None):
        system, contents = _to_contents(messages)
        config = self._config(system, schema, temperature, max_tokens)
        attempts = 1 + max(0, settings.LLM_MAX_RETRIES)
        first_error: LLMError | None = None
        models = self._models()

        for index, model in enumerate(models):
            for attempt in range(1, attempts + 1):
                started = time.time()
                try:
                    response = self._client.models.generate_content(
                        model=model, contents=contents, config=config
                    )
                    text = _extract_text(response)
                    result = _parse_structured(text, schema) if parse else text
                    self._log_usage(response, started)
                    if index:
                        logger.warning("Answered by the fallback model %s (primary %s was unavailable)", model, self.model)
                    return result
                except Exception as exc:  # translated into one typed error
                    error = exc if isinstance(exc, LLMError) else _translate(exc)
                    logger.warning(
                        "LLM call failed (model %s, attempt %d/%d): %s: %s | %s",
                        model, attempt, attempts, type(error).__name__, error.message, error.detail or "",
                    )
                    if error.retryable and attempt < attempts:
                        time.sleep(settings.LLM_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                        continue
                    first_error = first_error or error
                    if error.retryable and index + 1 < len(models):
                        break                      # still overloaded / rate limited: try the fallback model
                    # not retryable, or nothing left to try: report the PRIMARY model's problem
                    raise first_error from exc
        raise first_error or LLMError("The AI service did not answer.")

    def _config(self, system, schema, temperature, max_tokens) -> types.GenerateContentConfig:
        cfg: dict = {
            "max_output_tokens": max_tokens or settings.LLM_MAX_TOKENS,
            "thinking_config": types.ThinkingConfig(
                thinking_level=types.ThinkingLevel[settings.LLM_THINKING_LEVEL.upper()]
            ),
        }
        if system:
            cfg["system_instruction"] = system
        if schema:
            cfg["response_mime_type"] = "application/json"
            cfg["response_json_schema"] = schema
        # Gemini 3.x models document temperature/top_p as deprecated, so they are only
        # sent when LLM_SEND_SAMPLING_PARAMS=true (e.g. when you point at an older model).
        if settings.LLM_SEND_SAMPLING_PARAMS:
            cfg["temperature"] = settings.LLM_TEMPERATURE if temperature is None else temperature
            cfg["top_p"] = settings.LLM_TOP_P
        return types.GenerateContentConfig(**cfg)

    def _log_usage(self, response, started: float) -> None:
        usage = getattr(response, "usage_metadata", None)
        logger.info(
            "LLM ok: %d ms, tokens in=%s out=%s thinking=%s",
            int((time.time() - started) * 1000),
            getattr(usage, "prompt_token_count", "?"),
            getattr(usage, "candidates_token_count", "?"),
            getattr(usage, "thoughts_token_count", "?"),
        )


# ----------------------------------------------------------------------
# Message / response helpers
# ----------------------------------------------------------------------

def _to_contents(messages: list[dict]) -> tuple[str | None, list[types.Content]]:
    """
    Chat messages -> (system_instruction, contents).

    Gemini wants strictly alternating user/model turns starting with a user turn, so
    consecutive messages of one role are merged and an opening 'model' turn gets a
    short user turn in front of it.
    """
    system_parts: list[str] = []
    turns: list[list] = []  # [role, text]
    for message in messages:
        role = message.get("role", "user")
        text = str(message.get("content", ""))
        if role == "system":
            system_parts.append(text)
            continue
        role = "model" if role == "assistant" else "user"
        if turns and turns[-1][0] == role:
            turns[-1][1] += "\n\n" + text
        else:
            turns.append([role, text])

    if not turns:
        raise LLMResponseError("No message to send to the model.", retryable=False)
    if turns[0][0] == "model":
        turns.insert(0, ["user", "(The interview has started.)"])

    contents = [
        types.Content(role=role, parts=[types.Part.from_text(text=text)]) for role, text in turns
    ]
    return ("\n\n".join(system_parts) or None), contents


def _extract_text(response) -> str:
    feedback = getattr(response, "prompt_feedback", None)
    block = getattr(feedback, "block_reason", None)
    if block:
        raise LLMResponseError(
            f"The model refused the request ({_name(block)}).", detail=str(feedback), retryable=False
        )

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise LLMResponseError("The model returned no answer.")
    finish = _name(getattr(candidates[0], "finish_reason", None))

    if finish == "MAX_TOKENS":
        raise LLMResponseError(
            "The model's answer was cut off at the output limit. Raise LLM_MAX_TOKENS "
            "(thinking tokens count toward it) or lower LLM_THINKING_LEVEL.",
            retryable=False,
        )
    if finish in _BLOCKED_REASONS:
        raise LLMResponseError(f"The model's answer was blocked ({finish}).", retryable=False)

    text = (response.text or "").strip()   # joins the non-thinking text parts
    if not text:
        raise LLMResponseError("The model returned an empty answer.", detail=f"finish_reason={finish}")
    return text


def _parse_structured(text: str, schema: dict | None) -> dict:
    data = parse_json_loosely(text)
    if data is None:
        raise LLMResponseError("The model's answer was not valid JSON.", detail=text[:300])
    missing = [key for key in (schema or {}).get("required", []) if key not in data]
    if missing:
        raise LLMResponseError(
            f"The model's answer is missing fields: {', '.join(missing)}.", detail=text[:300]
        )
    return data


def parse_json_loosely(raw: str) -> dict | None:
    """Parse a JSON object, tolerating code fences / stray text. Returns None if there is none."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _name(value) -> str:
    return str(getattr(value, "name", value) or "")


# ----------------------------------------------------------------------
# Error translation
# ----------------------------------------------------------------------

def _translate(exc: Exception) -> LLMError:
    detail = f"{type(exc).__name__}: {exc}"[:500]

    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None) or 0
        status = str(getattr(exc, "status", "") or "")
        text = f"{status} {getattr(exc, 'message', '')} {exc}".upper()
        if code in (401, 403) or "API_KEY_INVALID" in text or "API KEY NOT VALID" in text:
            where = (
                "Check GEMINI_API_KEY." if settings.LLM_PROVIDER.lower() == "gemini"
                else "Check that the VM's service account has the 'Vertex AI User' role, the VM has the "
                     "cloud-platform access scope, and aiplatform.googleapis.com is enabled."
            )
            return LLMAuthError(f"Google rejected the credentials ({code}). {where}", detail=detail)
        if code == 404:
            return LLMModelNotFoundError(
                f"Model '{settings.active_model}' was not found"
                + (f" in location '{settings.VERTEX_LOCATION}'" if settings.LLM_PROVIDER.lower() == "vertex" else "")
                + ". Check the model id" + (" and VERTEX_LOCATION." if settings.LLM_PROVIDER.lower() == "vertex" else "."),
                detail=detail,
            )
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return LLMRateLimitError("The AI service is rate-limited right now. Try again shortly.", detail=detail)
        if code in (408, 504) or status == "DEADLINE_EXCEEDED":
            return LLMTimeoutError("The AI service timed out.", detail=detail)
        if code >= 500:
            return LLMUnavailableError(f"The AI service is unavailable ({code}).", detail=detail)
        return LLMError(f"The AI service rejected the request ({code} {status}).", detail=detail)

    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeoutError("The AI service did not answer in time.", detail=detail)
    if isinstance(exc, httpx.TransportError):
        return LLMConnectionError("Could not reach the AI service (network error).", detail=detail)
    if google_auth_exceptions and isinstance(exc, google_auth_exceptions.GoogleAuthError):
        return LLMAuthError(
            "Google Cloud credentials are missing or invalid. On the VM, attach a service account "
            "with the 'Vertex AI User' role; locally run 'gcloud auth application-default login'.",
            detail=detail,
        )
    if isinstance(exc, ValueError):
        return LLMConfigError(f"LLM client is misconfigured: {exc}", detail=detail)
    return LLMError("Unexpected error while calling the AI service.", detail=detail)


# ----------------------------------------------------------------------
# Shared instance
# ----------------------------------------------------------------------
_provider: GeminiProvider | None = None
_lock = threading.Lock()


def get_llm() -> GeminiProvider:
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = GeminiProvider()
    return _provider


def reset_llm() -> None:
    """Forget the cached client (tests, or after changing settings)."""
    global _provider
    with _lock:
        _provider = None
