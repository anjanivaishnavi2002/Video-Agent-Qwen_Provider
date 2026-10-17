"""OpenAI and DeepSeek (DeepSeek speaks the OpenAI chat-completions protocol)."""
import httpx

from app.providers.base import Completion, Provider, ProviderError, check_structured, http_error, schema_instruction


class OpenAICompatible(Provider):
    def __init__(self, name: str, api_key: str, model: str, url: str, *, token_param: str,
                 http: httpx.Client | None = None):
        self.name, self.model, self._key, self._url, self._token_param, self._http = name, model, api_key, url, token_param, http

    def complete(self, messages, *, schema, temperature, max_tokens, timeout) -> Completion:
        msgs = [{"role": m.get("role", "user") if m.get("role") != "model" else "assistant",
                 "content": str(m.get("content", ""))} for m in messages]
        payload = {"model": self.model, "messages": msgs, "temperature": temperature, self._token_param: max_tokens}
        if schema:
            msgs.insert(0, {"role": "system", "content": schema_instruction(schema)})
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            post = self._http.post if self._http is not None else httpx.post
            response = post(self._url, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name} did not answer in time.", kind="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach {self.name} ({type(exc).__name__}).", kind="connection", retryable=True) from exc
        if response.status_code >= 400:
            raise http_error(response.status_code, response.text, self.name)
        try:
            data = response.json()
            choice = data["choices"][0]
            text = (choice.get("message") or {}).get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{self.name} returned an unreadable answer.", kind="bad_response", retryable=True) from exc
        if choice.get("finish_reason") == "length":
            raise ProviderError(f"{self.name}'s answer was cut off at the output limit.", kind="bad_response", retryable=True)
        text = text.strip()
        if not text:
            raise ProviderError(f"{self.name} returned an empty answer.", kind="bad_response", retryable=True)
        if schema:
            text = check_structured(text, schema)
        usage = data.get("usage") or {}
        return Completion(text, data.get("model") or self.model, usage.get("prompt_tokens"), usage.get("completion_tokens"))
