"""Anthropic Claude (Messages API). Structured output uses a forced tool call, so the reply is JSON by construction."""
import json

import httpx

from app.providers.base import Completion, Provider, ProviderError, http_error, split_system

URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, http: httpx.Client | None = None):
        self.model, self._key, self._http = model, api_key, http

    def complete(self, messages, *, schema, temperature, max_tokens, timeout) -> Completion:
        system, turns = split_system(messages)
        payload = {"model": self.model, "max_tokens": max_tokens, "messages": turns, "temperature": min(temperature, 1.0)}
        if system:
            payload["system"] = system
        if schema:
            payload["tools"] = [{"name": "respond", "description": "Return the answer as structured data.",
                                 "input_schema": schema}]
            payload["tool_choice"] = {"type": "tool", "name": "respond"}
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            post = self._http.post if self._http is not None else httpx.post
            response = post(URL, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError("anthropic did not answer in time.", kind="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach anthropic ({type(exc).__name__}).", kind="connection", retryable=True) from exc
        if response.status_code >= 400:
            # 529 = Anthropic "overloaded"
            if response.status_code == 529:
                raise ProviderError("anthropic is overloaded (529).", kind="unavailable", retryable=True)
            raise http_error(response.status_code, response.text, "anthropic")
        try:
            data = response.json()
            blocks = data["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("anthropic returned an unreadable answer.", kind="bad_response", retryable=True) from exc
        if data.get("stop_reason") == "max_tokens":
            raise ProviderError("anthropic's answer was cut off at the output limit.", kind="bad_response", retryable=True)
        if schema:
            tool = next((b for b in blocks if b.get("type") == "tool_use"), None)
            if not tool or not isinstance(tool.get("input"), dict):
                raise ProviderError("anthropic returned no structured answer.", kind="bad_response", retryable=True)
            text = json.dumps(tool["input"], ensure_ascii=False)
        else:
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            if not text:
                raise ProviderError("anthropic returned an empty answer.", kind="bad_response", retryable=True)
        usage = data.get("usage") or {}
        return Completion(text, data.get("model") or self.model, usage.get("input_tokens"), usage.get("output_tokens"))
