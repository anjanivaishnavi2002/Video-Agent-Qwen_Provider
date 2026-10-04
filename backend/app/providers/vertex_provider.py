"""
Qwen (or another open model) on Google Cloud Vertex AI - used instead of a local Ollama.

    LLM_PROVIDER=vertex   VERTEX_PROJECT_ID=...   VERTEX_LOCATION=...   VERTEX_MODEL=<model id from Model Garden>

Two ways to reach a model, both through Vertex AI's OpenAI-compatible chat-completions API:
  * a managed model from Model Garden (pay per use, nothing to deploy)  -> leave VERTEX_ENDPOINT_ID empty
  * a model you deployed to your own endpoint                           -> set VERTEX_ENDPOINT_ID
or set VERTEX_BASE_URL yourself for any other OpenAI-compatible URL.

Authentication: the VM's service account (Application Default Credentials). It needs the role
"Vertex AI User" and the VM must have the cloud-platform access scope. No API key is stored anywhere.

Same public shape as OllamaProvider (`chat`, `chat_json`, `ping`) and the same typed errors. Failures are raised,
never replaced by invented answers.
"""
import json
import logging
import threading
import time

import httpx

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
from app.providers.ollama_provider import _parse_structured

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class _Credentials:
    """Application Default Credentials with automatic token refresh (thread-safe)."""

    def __init__(self):
        self._creds = None
        self._project = None
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            try:
                import google.auth
                from google.auth.transport.requests import Request

                if self._creds is None:
                    self._creds, self._project = google.auth.default(scopes=_SCOPES)
                if not self._creds.valid:
                    self._creds.refresh(Request())
                return self._creds.token
            except Exception as exc:   # no credentials, no metadata server, token refused ...
                raise LLMAuthError(
                    "Could not get Google Cloud credentials. On the VM, give its service account the "
                    "'Vertex AI User' role and the cloud-platform access scope.",
                    detail=f"{type(exc).__name__}: {exc}"[:400],
                ) from exc

    @property
    def project(self) -> str | None:
        return self._project


_credentials = _Credentials()


def _host(location: str) -> str:
    return "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"


def build_base_url(project: str) -> str:
    """The URL that '/chat/completions' is appended to."""
    override = settings.VERTEX_BASE_URL.strip().rstrip("/")
    if override:
        return override
    location = settings.VERTEX_LOCATION.strip() or "us-central1"
    root = f"https://{_host(location)}/v1/projects/{project}/locations/{location}"
    endpoint = settings.VERTEX_ENDPOINT_ID.strip()
    return f"{root}/endpoints/{endpoint}" if endpoint else f"{root}/endpoints/openapi"


def _translate(exc: Exception, model: str) -> LLMError:
    detail = f"{type(exc).__name__}: {exc}"[:500]
    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeoutError("The Vertex AI model did not answer in time.", detail=detail)
    if isinstance(exc, httpx.TransportError):
        return LLMConnectionError("Could not reach Vertex AI. Check the VM's internet access.", detail=detail)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        body = exc.response.text[:400]
        detail = f"{detail} | {body}"
        if code in (401, 403):
            return LLMAuthError(
                "Vertex AI refused the request. Check the service account's 'Vertex AI User' role, the VM's "
                "cloud-platform access scope, that the Vertex AI API is enabled, and that the model is enabled "
                "in Model Garden.", detail=detail)
        if code == 404:
            return LLMModelNotFoundError(
                f"Vertex AI could not find model/endpoint '{model}' in this project and location. "
                "Check VERTEX_MODEL, VERTEX_LOCATION and VERTEX_ENDPOINT_ID.", detail=detail)
        if code == 429:
            return LLMRateLimitError("Vertex AI is rate limiting requests. Try again in a moment.", detail=detail)
        if code >= 500:
            return LLMUnavailableError(f"Vertex AI reported an error ({code}).", detail=detail)
        return LLMError(f"Vertex AI rejected the request ({code}).", detail=detail)
    return LLMError("Unexpected error while calling Vertex AI.", detail=detail)


class VertexProvider:
    def __init__(self):
        self.backend = "vertex"
        self.model = settings.active_model
        if not self.model:
            raise LLMConfigError(
                "VERTEX_MODEL is not set. Copy the model id from Vertex AI > Model Garden into VERTEX_MODEL.")
        self._project = settings.VERTEX_PROJECT_ID.strip()
        self._http = httpx.Client(timeout=settings.VERTEX_TIMEOUT_SECONDS)
        self._token = _credentials.token      # replaceable in tests
        logger.info("LLM ready: backend=vertex model=%s location=%s", self.model, settings.VERTEX_LOCATION)

    # ---- public API -----------------------------------------------------------------------
    def chat(self, messages: list[dict], *, schema: dict | None = None, temperature: float | None = None) -> str:
        return self._run(messages, schema, temperature, parse=False)

    def chat_json(self, messages: list[dict], schema: dict, **kwargs) -> dict:
        return self._run(messages, schema, kwargs.get("temperature"), parse=True)

    def ping(self) -> dict:
        """One tiny real call: proves credentials, project, location, model id and network (used by /health?deep=true)."""
        started = time.time()
        text = self._run([{"role": "user", "content": "Reply with the single word: OK"}], None, None,
                         parse=False, max_tokens=16)
        return {"ok": True, "latency_ms": int((time.time() - started) * 1000), "reply": text[:20]}

    # ---- internals ------------------------------------------------------------------------
    def _url(self) -> str:
        project = self._project or _credentials.project
        if not project and not settings.VERTEX_BASE_URL.strip():
            self._token()   # loads ADC, which also tells us the project
            project = _credentials.project
        if not project and not settings.VERTEX_BASE_URL.strip():
            raise LLMConfigError("VERTEX_PROJECT_ID is not set.")
        return build_base_url(project or "") + "/chat/completions"

    def _payload(self, messages, schema, temperature, max_tokens) -> dict:
        msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in messages]
        payload = {
            "model": self.model,
            "messages": msgs,
            "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
            "top_p": settings.LLM_TOP_P,
            "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
            "stream": False,
        }
        if schema:
            mode = settings.VERTEX_JSON_MODE.strip().lower()
            # The schema is always spelled out in the prompt: not every hosted model enforces response_format.
            note = ("Reply with ONE JSON object only (no code fences, no extra text) that follows this JSON schema:\n"
                    + json.dumps(schema))
            payload["messages"] = [{"role": "system", "content": note}] + msgs
            if mode == "json_schema":
                payload["response_format"] = {"type": "json_schema",
                                              "json_schema": {"name": "reply", "schema": schema}}
            elif mode == "json_object":
                payload["response_format"] = {"type": "json_object"}
        return payload

    def _run(self, messages, schema, temperature, *, parse: bool, max_tokens: int | None = None):
        payload = self._payload(messages, schema, temperature, max_tokens)
        attempts = 1 + max(0, settings.LLM_MAX_RETRIES)

        for attempt in range(1, attempts + 1):
            started = time.time()
            try:
                url = self._url()
                headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
                response = self._http.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                choice = (data.get("choices") or [{}])[0]
                text = ((choice.get("message") or {}).get("content") or "").strip()
                if not text:
                    raise LLMResponseError("The model returned an empty answer.", detail=str(data)[:300])
                if choice.get("finish_reason") == "length" and parse:
                    raise LLMResponseError(
                        "The model's answer was cut off at the output limit. Raise LLM_MAX_TOKENS.",
                        detail=text[:300], retryable=False)
                result = _parse_structured(text, schema) if parse else text
                usage = data.get("usage") or {}
                logger.info("LLM ok: %d ms, tokens in=%s out=%s", int((time.time() - started) * 1000),
                            usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?"))
                return result
            except Exception as exc:
                error = exc if isinstance(exc, LLMError) else _translate(exc, self.model)
                logger.warning("LLM call failed (attempt %d/%d): %s: %s | %s", attempt, attempts,
                               type(error).__name__, error.message, error.detail or "")
                if error.retryable and attempt < attempts:
                    time.sleep(settings.LLM_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                    continue
                if error is exc:
                    raise
                raise error from exc
