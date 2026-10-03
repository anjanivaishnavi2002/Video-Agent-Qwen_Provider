"""
Qwen (or any model) served by Ollama - the only LLM back end.

    OLLAMA_HOST=http://ollama:11434   OLLAMA_MODEL=qwen2.5:3b-instruct

Public API: `chat`, `chat_json`, `ping`, plus typed errors (llm_errors.py), so the interview engine, resume
analysis and the report never depend on the model details. Structured output uses Ollama's JSON-schema
`format`. Failures are raised, never replaced by invented answers.
"""
import json
import logging
import re
import threading
import time

import httpx

from app.config import settings
from app.providers.llm_errors import (
    LLMConfigError,
    LLMConnectionError,
    LLMError,
    LLMModelNotFoundError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)

logger = logging.getLogger(__name__)


def _translate(exc: Exception, model: str, host: str) -> LLMError:
    detail = f"{type(exc).__name__}: {exc}"[:500]
    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeoutError(
            "The local model did not answer in time (the first reply after a restart is the slowest).", detail=detail)
    if isinstance(exc, httpx.ConnectError):
        return LLMConnectionError(
            f"Could not reach Ollama at {host}. Check that the ollama container is running.", detail=detail)
    if isinstance(exc, httpx.TransportError):
        return LLMConnectionError("Network error while talking to Ollama.", detail=detail)
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        body = exc.response.text[:300]
        if code == 404 or "not found" in body.lower():
            return LLMModelNotFoundError(
                f"Model '{model}' is not installed in Ollama. Run: ollama pull {model}", detail=detail + " | " + body)
        if code >= 500:
            return LLMUnavailableError(f"Ollama reported an error ({code}).", detail=detail + " | " + body)
        return LLMError(f"Ollama rejected the request ({code}).", detail=detail + " | " + body)
    return LLMError("Unexpected error while calling the local model.", detail=detail)


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


class OllamaProvider:
    def __init__(self):
        self.backend = "ollama"
        self.host = settings.OLLAMA_HOST.rstrip("/")
        self.model = settings.active_model
        if not self.host:
            raise LLMConfigError("OLLAMA_HOST is not set.")
        if not self.model:
            raise LLMConfigError("No model configured: set OLLAMA_MODEL (for example qwen2.5:3b-instruct).")
        self._http = httpx.Client(timeout=settings.OLLAMA_TIMEOUT_SECONDS)
        logger.info("LLM ready: backend=ollama model=%s host=%s", self.model, self.host)

    # ---- public API -------------------------------------------------------
    def chat(self, messages: list[dict], *, schema: dict | None = None, temperature: float | None = None) -> str:
        return self._run(messages, schema, temperature, parse=False)

    def chat_json(self, messages: list[dict], schema: dict, **kwargs) -> dict:
        return self._run(messages, schema, kwargs.get("temperature"), parse=True)

    def ping(self) -> dict:
        """Is Ollama up, is the model installed, and does it answer? (used by /health?deep=true)"""
        started = time.time()
        try:
            response = self._http.get(f"{self.host}/api/tags")
            response.raise_for_status()
        except Exception as exc:
            raise _translate(exc, self.model, self.host) from exc
        names = {m.get("name", "") for m in response.json().get("models", [])}
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in names and self.model not in names:
            raise LLMModelNotFoundError(
                f"Model '{self.model}' is not installed in Ollama. Run: ollama pull {self.model}",
                detail=f"installed: {sorted(names)}")
        text = self._run([{"role": "user", "content": "Reply with the single word: OK"}], None, None,
                         parse=False, max_tokens=16)
        return {"ok": True, "latency_ms": int((time.time() - started) * 1000), "reply": text[:20]}

    # ---- internals ------------------------------------------------------------------------
    def _run(self, messages, schema, temperature, *, parse: bool, max_tokens: int | None = None):
        payload = {
            "model": self.model,
            "messages": [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in messages],
            "stream": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {
                "temperature": settings.LLM_TEMPERATURE if temperature is None else temperature,
                "top_p": settings.LLM_TOP_P,
                "num_ctx": settings.OLLAMA_NUM_CTX,
                "num_predict": max_tokens or settings.LLM_MAX_TOKENS,
            },
        }
        if schema:
            payload["format"] = schema
        attempts = 1 + max(0, settings.LLM_MAX_RETRIES)

        for attempt in range(1, attempts + 1):
            started = time.time()
            try:
                response = self._http.post(f"{self.host}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
                text = ((data.get("message") or {}).get("content") or "").strip()
                if not text:
                    raise LLMResponseError("The model returned an empty answer.", detail=str(data)[:300])
                if data.get("done_reason") == "length" and parse:
                    raise LLMResponseError(
                        "The model's answer was cut off at the output limit. Raise LLM_MAX_TOKENS.",
                        detail=text[:300], retryable=False)
                result = _parse_structured(text, schema) if parse else text
                logger.info("LLM ok: %d ms, tokens in=%s out=%s", int((time.time() - started) * 1000),
                            data.get("prompt_eval_count", "?"), data.get("eval_count", "?"))
                return result
            except Exception as exc:
                error = exc if isinstance(exc, LLMError) else _translate(exc, self.model, self.host)
                logger.warning("LLM call failed (attempt %d/%d): %s: %s | %s", attempt, attempts,
                               type(error).__name__, error.message, error.detail or "")
                if error.retryable and attempt < attempts:
                    time.sleep(settings.LLM_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                    continue
                if error is exc:
                    raise
                raise error from exc


# ----------------------------------------------------------------------
# Shared instance
# ----------------------------------------------------------------------
_provider: OllamaProvider | None = None
_lock = threading.Lock()


def get_llm() -> OllamaProvider:
    """The shared model client (created on first use)."""
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = OllamaProvider()
    return _provider


def reset_llm() -> None:
    """Forget the cached client (tests, or after changing settings)."""
    global _provider
    with _lock:
        _provider = None
