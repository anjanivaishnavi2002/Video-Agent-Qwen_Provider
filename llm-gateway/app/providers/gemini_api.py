"""Gemini through the Gemini API (API key). This is separate from Vertex AI, which the backend calls directly."""
import httpx

from app.providers.base import Completion, Provider, ProviderError, check_structured, http_error, split_system

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiApiProvider(Provider):
    name = "gemini_api"

    def __init__(self, api_key: str, model: str, http: httpx.Client | None = None):
        self.model, self._key, self._http = model, api_key, http

    def complete(self, messages, *, schema, temperature, max_tokens, timeout) -> Completion:
        system, turns = split_system(messages)
        contents = [{"role": "model" if t["role"] == "assistant" else "user", "parts": [{"text": t["content"]}]}
                    for t in turns]
        config = {"maxOutputTokens": max_tokens, "temperature": temperature}
        if schema:
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = schema
        payload = {"contents": contents, "generationConfig": config}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        url = f"{BASE}/{self.model}:generateContent"
        try:
            post = self._http.post if self._http is not None else httpx.post
            response = post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError("gemini_api did not answer in time.", kind="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach gemini_api ({type(exc).__name__}).", kind="connection", retryable=True) from exc
        if response.status_code >= 400:
            raise http_error(response.status_code, response.text, "gemini_api")
        try:
            data = response.json()
            candidate = (data.get("candidates") or [])[0]
        except (ValueError, IndexError, TypeError) as exc:
            raise ProviderError("gemini_api returned no answer.", kind="bad_response", retryable=True) from exc
        finish = str(candidate.get("finishReason", ""))
        if finish == "MAX_TOKENS":
            raise ProviderError("gemini_api's answer was cut off at the output limit.", kind="bad_response", retryable=True)
        if finish in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"):
            raise ProviderError(f"gemini_api blocked the answer ({finish}).", kind="blocked")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not text:
            raise ProviderError("gemini_api returned an empty answer.", kind="bad_response", retryable=True)
        if schema:
            text = check_structured(text, schema)
        usage = data.get("usageMetadata") or {}
        return Completion(text, self.model, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))
