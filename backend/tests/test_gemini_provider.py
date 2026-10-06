"""Gemini on Vertex AI: client wiring (ADC, no key), structured output, message mapping, typed errors."""
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from app.config import settings
from app.providers import factory, gemini_provider
from app.providers.llm_errors import (
    LLMAuthError, LLMConfigError, LLMModelNotFoundError, LLMRateLimitError, LLMResponseError,
)

SCHEMA = {"type": "object", "properties": {"spoken_text": {"type": "string"}}, "required": ["spoken_text"]}


def _response(text, finish="STOP"):
    return SimpleNamespace(text=text, prompt_feedback=None, usage_metadata=None,
                           candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))])


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "VERTEX_AI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "LLM_MODEL", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODEL", "")
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_RETRY_BACKOFF_SECONDS", 0)
    holder = SimpleNamespace(models=FakeModels([]))
    monkeypatch.setattr(gemini_provider, "build_client", lambda: holder)
    p = gemini_provider.GeminiProvider()
    p.holder = holder
    return p


def test_client_uses_vertex_ai_with_project_and_location_from_settings(monkeypatch):
    seen = {}
    monkeypatch.setattr(gemini_provider.genai, "Client", lambda **kw: seen.update(kw) or "client")
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "my-proj")
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_LOCATION", "europe-west4")
    assert gemini_provider.build_client() == "client"
    assert seen["vertexai"] is True and seen["project"] == "my-proj" and seen["location"] == "europe-west4"
    assert "api_key" not in seen                       # Application Default Credentials only


def test_missing_project_is_a_clear_config_error(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "")
    with pytest.raises(LLMConfigError):
        gemini_provider.build_client()


def test_structured_reply_uses_json_schema_and_maps_roles(provider):
    provider.holder.models.outcomes = [_response('{"spoken_text": "Hello Asha"}')]
    out = provider.chat_json(
        [{"role": "system", "content": "SYS"}, {"role": "assistant", "content": "earlier question"},
         {"role": "user", "content": "my answer"}], SCHEMA)
    assert out == {"spoken_text": "Hello Asha"}
    call = provider.holder.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    assert call["config"].system_instruction == "SYS"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == SCHEMA
    roles = [c.role for c in call["contents"]]
    assert roles == ["user", "model", "user"]          # an opening model turn gets a user turn in front


def test_invalid_json_and_missing_fields_are_errors_not_made_up_answers(provider):
    provider.holder.models.outcomes = [_response("not json")] * 2
    with pytest.raises(LLMResponseError):
        provider.chat_json([{"role": "user", "content": "hi"}], SCHEMA)
    provider.holder.models.outcomes = [_response('{"other": 1}')] * 2
    with pytest.raises(LLMResponseError):
        provider.chat_json([{"role": "user", "content": "hi"}], SCHEMA)


def test_truncated_answer_is_reported(provider):
    provider.holder.models.outcomes = [_response('{"spoken_text": "x', finish="MAX_TOKENS")]
    with pytest.raises(LLMResponseError) as info:
        provider.chat_json([{"role": "user", "content": "hi"}], SCHEMA)
    assert "LLM_MAX_TOKENS" in info.value.message


def test_rate_limit_is_retried_then_succeeds(provider):
    provider.holder.models.outcomes = [genai_errors.APIError(429, {"error": {"status": "RESOURCE_EXHAUSTED"}}),
                                       _response('{"spoken_text": "ok"}')]
    assert provider.chat_json([{"role": "user", "content": "hi"}], SCHEMA)["spoken_text"] == "ok"
    assert len(provider.holder.models.calls) == 2


def test_auth_and_model_errors_are_typed_and_not_retried(provider):
    provider.holder.models.outcomes = [genai_errors.APIError(403, {"error": {"status": "PERMISSION_DENIED"}})]
    with pytest.raises(LLMAuthError) as info:
        provider.chat([{"role": "user", "content": "hi"}])
    assert "aiplatform.user" in info.value.message and len(provider.holder.models.calls) == 1
    provider.holder.models.outcomes = [genai_errors.APIError(404, {"error": {"status": "NOT_FOUND"}})]
    with pytest.raises(LLMModelNotFoundError):
        provider.chat([{"role": "user", "content": "hi"}])


def test_persistent_rate_limit_raises_rate_limit_error(provider):
    provider.holder.models.outcomes = [genai_errors.APIError(429, {"error": {}})] * 2
    with pytest.raises(LLMRateLimitError):
        provider.chat([{"role": "user", "content": "hi"}])


def test_factory_picks_provider_from_settings(monkeypatch):
    monkeypatch.setattr(gemini_provider, "build_client", lambda: object())
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    factory.reset_llm()
    try:
        assert isinstance(factory.get_llm(), gemini_provider.GeminiProvider)
    finally:
        factory.reset_llm()


def test_qwen_ollama_is_disabled_unless_explicitly_enabled(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "ENABLE_OLLAMA", False)
    assert settings.llm_backend == "gemini"
