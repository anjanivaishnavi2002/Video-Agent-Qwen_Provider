"""
The ONLY place that talks to the local Qwen model (through Ollama).
Interview turns and resume analysis both go through this class.
"""
import json
import logging
import re

from ollama import Client

from app.config import settings

logger = logging.getLogger(__name__)


class QwenProvider:
    def __init__(self):
        self._client = Client(
            host=settings.OLLAMA_HOST, timeout=settings.LLM_TIMEOUT_SECONDS
        )

    def chat(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        temperature: float | None = None,
    ) -> str:
        """Send chat messages, return the raw reply text.

        When `schema` is given, Ollama constrains the output to that JSON schema.
        """
        response = self._client.chat(
            model=settings.LLM_MODEL,
            messages=messages,
            format=schema,
            keep_alive=settings.LLM_KEEP_ALIVE,
            options={
                "temperature": (
                    settings.LLM_TEMPERATURE if temperature is None else temperature
                ),
                "top_p": settings.LLM_TOP_P,
                "num_ctx": settings.LLM_NUM_CTX,
                "num_predict": settings.LLM_MAX_TOKENS,
            },
        )
        return response["message"]["content"].strip()

    def chat_json(self, messages: list[dict], schema: dict, **kwargs) -> dict | None:
        """Like chat(), but returns a parsed dict (None if the reply is not valid JSON)."""
        raw = self.chat(messages, schema=schema, **kwargs)
        return parse_json_loosely(raw)


def parse_json_loosely(raw: str) -> dict | None:
    """Parse a JSON object even if the model wrapped it in text or code fences."""
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


_provider: QwenProvider | None = None


def get_llm() -> QwenProvider:
    global _provider
    if _provider is None:
        _provider = QwenProvider()
    return _provider
