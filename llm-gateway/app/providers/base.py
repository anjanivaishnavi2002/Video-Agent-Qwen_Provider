"""Provider interface + typed errors. A new vendor = one class implementing `Provider`, registered in router.build_providers."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderError(Exception):
    """A provider could not answer. `kind` is a short stable label (auth, rate_limit, unavailable, timeout, ...)."""

    def __init__(self, message: str, *, kind: str = "error", retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


@dataclass
class Completion:
    text: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None


class Provider(ABC):
    name: str
    model: str

    @abstractmethod
    def complete(self, messages: list[dict], *, schema: dict | None, temperature: float, max_tokens: int,
                 timeout: float) -> Completion: ...


# ---- shared helpers -------------------------------------------------------------------------------------

def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """-> (system text, strictly alternating user/assistant turns starting with a user turn)."""
    system: list[str] = []
    turns: list[dict] = []
    for message in messages:
        role = message.get("role", "user")
        text = str(message.get("content", ""))
        if role == "system":
            system.append(text)
            continue
        role = "assistant" if role in ("assistant", "model") else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + text
        else:
            turns.append({"role": role, "content": text})
    if not turns:
        raise ProviderError("No message to send.", kind="bad_request")
    if turns[0]["role"] == "assistant":
        turns.insert(0, {"role": "user", "content": "(The interview has started.)"})
    return "\n\n".join(system), turns


def schema_instruction(schema: dict) -> str:
    return ("Reply with ONLY a single JSON object (no markdown, no commentary) that matches this JSON schema:\n"
            + json.dumps(schema, separators=(",", ":")))


def parse_json_loosely(raw: str) -> dict | None:
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


def check_structured(text: str, schema: dict) -> str:
    """Return clean JSON text, or raise a retryable bad_response (the router then tries the next provider)."""
    data = parse_json_loosely(text)
    if data is None:
        raise ProviderError("The answer was not valid JSON.", kind="bad_response", retryable=True)
    missing = [k for k in schema.get("required", []) if k not in data]
    if missing:
        raise ProviderError(f"The answer is missing fields: {', '.join(missing)}.", kind="bad_response", retryable=True)
    return json.dumps(data, ensure_ascii=False)


def http_error(status: int, body_text: str, provider: str) -> ProviderError:
    """Translate an HTTP failure. Never includes request content; only a short provider message."""
    detail = (body_text or "")[:200].replace("\n", " ")
    if status in (401, 403):
        return ProviderError(f"{provider} rejected the API key ({status}).", kind="auth")
    if status == 404:
        return ProviderError(f"{provider} model not found ({status}). Check the model name.", kind="model_not_found")
    if status == 429:
        return ProviderError(f"{provider} is rate-limited ({status}).", kind="rate_limit", retryable=True)
    if status in (408, 504):
        return ProviderError(f"{provider} timed out ({status}).", kind="timeout", retryable=True)
    if status >= 500:
        return ProviderError(f"{provider} is unavailable ({status}).", kind="unavailable", retryable=True)
    return ProviderError(f"{provider} rejected the request ({status}) {detail}".strip(), kind="bad_request")
