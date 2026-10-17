"""
Talks to the separate llm-gateway service, and wraps the primary model (Vertex AI Gemini) with it.

    RoutedLLM(primary=GeminiProvider(), gateway=GatewayClient(...), share=0..100)

* share=0   : Vertex answers; the gateway is used only when Vertex fails (overloaded, rate-limited, unreachable,
              mis-configured). Safety blocks are NOT sent to another model.
* share=N   : N% of requests go to the gateway first (spread over OpenAI / Anthropic / Gemini API / DeepSeek), the rest to
              Vertex first. The other side is always the fallback.
The rest of the app still only sees `get_llm()` -> `.chat()` / `.chat_json()` / `.ping()`.
"""
import logging
import random

import httpx

from app.config import settings
from app.providers.gemini_provider import _parse_structured
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

logger = logging.getLogger(__name__)


def _should_fall_back(error: LLMError) -> bool:
    """Another model may succeed where this one failed - except when it deliberately refused (safety block)."""
    if isinstance(error, (LLMAuthError, LLMModelNotFoundError, LLMConfigError)):
        return True
    return bool(error.retryable)


class GatewayClient:
    def __init__(self, url: str, *, http: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self._http = http

    def _headers(self) -> dict:
        headers: dict = {}
        mode = settings.LLM_GATEWAY_AUTH_MODE.lower()
        if mode in ("token", "both") and settings.LLM_GATEWAY_TOKEN:
            headers["X-Gateway-Token"] = settings.LLM_GATEWAY_TOKEN
        if mode in ("iam", "both"):
            import google.auth.transport.requests
            import google.oauth2.id_token

            token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), self.url)
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def chat(self, messages: list[dict], *, schema: dict | None = None, temperature: float | None = None,
             max_tokens: int | None = None) -> str:
        payload: dict = {"messages": [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
                                      for m in messages]}
        if schema:
            payload["schema"] = schema
        if temperature is not None:
            payload["temperature"] = temperature
        payload["max_tokens"] = max_tokens or settings.LLM_MAX_TOKENS
        try:
            post = self._http.post if self._http is not None else httpx.post
            response = post(f"{self.url}/v1/chat", json=payload, headers=self._headers(),
                            timeout=settings.LLM_GATEWAY_TIMEOUT_SECONDS)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("The LLM gateway did not answer in time.", detail=type(exc).__name__) from exc
        except httpx.HTTPError as exc:
            raise LLMConnectionError("Could not reach the LLM gateway.", detail=type(exc).__name__) from exc
        except Exception as exc:                                     # e.g. ID-token fetch failure
            raise LLMAuthError("Could not authenticate to the LLM gateway.", detail=f"{type(exc).__name__}") from exc
        if response.status_code == 200:
            data = response.json()
            logger.info("Gateway answered: provider=%s model=%s fallback_inside_gateway=%s",
                        data.get("provider"), data.get("model"), data.get("fallback"))
            return str(data.get("text", ""))
        if response.status_code in (401, 403):
            raise LLMAuthError("The LLM gateway rejected our credentials.", detail=str(response.status_code))
        if response.status_code == 429:
            raise LLMRateLimitError("The LLM gateway is rate-limited.", detail="429")
        try:
            body = response.json()
        except ValueError:
            body = {}
        attempts = body.get("attempts") or []
        detail = "; ".join(f"{a.get('provider')}:{a.get('kind')}" for a in attempts)[:300]
        if response.status_code == 503 and not attempts:
            raise LLMConfigError("The LLM gateway has no providers configured.", detail=body.get("error"))
        if response.status_code >= 500:
            raise LLMUnavailableError("No gateway provider could answer.", detail=detail or str(response.status_code),
                                      retryable=bool(body.get("retryable", True)))
        raise LLMError(f"The LLM gateway rejected the request ({response.status_code}).", detail=detail)

    def status(self) -> dict:
        post = self._http.get if self._http is not None else httpx.get
        response = post(f"{self.url}/v1/providers", headers=self._headers(), timeout=10)
        response.raise_for_status()
        return response.json()


class RoutedLLM:
    def __init__(self, primary, gateway: GatewayClient, share: int = 0):
        self.primary = primary
        self.gateway = gateway
        self.share = max(0, min(100, int(share)))
        self.backend = f"{getattr(primary, 'backend', 'gemini')}+gateway"
        self.model = getattr(primary, "model", "")

    # ---- public API (same shape as GeminiProvider / OllamaProvider) --------------------------------------
    def chat(self, messages, *, schema=None, temperature=None) -> str:
        return self._route(messages, schema, temperature, None, parse=False)

    def chat_json(self, messages, schema, **kwargs) -> dict:
        return self._route(messages, schema, kwargs.get("temperature"), kwargs.get("max_tokens"), parse=True)

    def ping(self) -> dict:
        result = dict(self.primary.ping())
        try:
            result["gateway"] = {"ok": True, **self.gateway.status(), "share_percent": self.share}
        except Exception as exc:
            result["gateway"] = {"ok": False, "error": f"{type(exc).__name__}", "share_percent": self.share}
        return result

    # ---- routing -----------------------------------------------------------------------------------
    def _route(self, messages, schema, temperature, max_tokens, *, parse: bool):
        gateway_first = self.share > 0 and random.uniform(0, 100) < self.share
        sides = ["gateway", "primary"] if gateway_first else ["primary", "gateway"]
        errors: dict[str, LLMError] = {}
        for index, side in enumerate(sides):
            try:
                result = self._call(side, messages, schema, temperature, max_tokens, parse)
            except LLMError as exc:
                logger.warning("LLM %s failed: %s: %s | %s", side, type(exc).__name__, exc.message, exc.detail or "")
                errors[side] = exc
                if index == 0 and _should_fall_back(exc):
                    continue
                # Both failed, or the first failure is one we must not shop around (e.g. a safety block).
                # When both failed, report the PRIMARY (Vertex) problem: it is the one the operator configured.
                raise errors.get("primary", exc) from exc
            if index:
                logger.warning("Answered by %s after %s failed", side, sides[0])
            return result
        raise LLMError("The AI service did not answer.")   # unreachable; keeps the type checker honest

    def _call(self, side, messages, schema, temperature, max_tokens, parse):
        if side == "primary":
            if parse:
                kwargs = {"temperature": temperature} if temperature is not None else {}
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
                return self.primary.chat_json(messages, schema, **kwargs)
            return self.primary.chat(messages, schema=schema, temperature=temperature)
        text = self.gateway.chat(messages, schema=schema, temperature=temperature, max_tokens=max_tokens)
        return _parse_structured(text, schema) if parse else text
