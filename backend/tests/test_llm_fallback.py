"""Overloaded main model -> retries, then the fallback model; real errors are never hidden."""
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from app.config import settings
from app.providers import gemini_provider
from app.providers.llm_errors import LLMAuthError, LLMUnavailableError


def _ok(text="OK"):
    return SimpleNamespace(
        prompt_feedback=None, text=text, usage_metadata=None,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
    )


def _api_error(code, status):
    return genai_errors.APIError(code, {"error": {"code": code, "status": status, "message": "boom"}})


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        result = self.behaviour(model)
        if isinstance(result, Exception):
            raise result
        return result


def _provider(monkeypatch, behaviour, fallback="fallback-model"):
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODEL", fallback)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(gemini_provider, "build_client", lambda: SimpleNamespace(models=FakeModels(behaviour)))
    gemini_provider.reset_llm()
    return gemini_provider.GeminiProvider()


def test_overloaded_main_model_falls_back(monkeypatch):
    provider = _provider(monkeypatch, lambda m: _api_error(503, "UNAVAILABLE") if m != "fallback-model" else _ok())
    assert provider.chat([{"role": "user", "content": "hi"}]) == "OK"
    assert provider._client.models.calls == [provider.model, provider.model, "fallback-model"]


def test_failure_of_both_reports_the_main_models_error(monkeypatch):
    provider = _provider(monkeypatch, lambda m: _api_error(503, "UNAVAILABLE"))
    with pytest.raises(LLMUnavailableError):
        provider.chat([{"role": "user", "content": "hi"}])


def test_auth_errors_do_not_trigger_the_fallback(monkeypatch):
    provider = _provider(monkeypatch, lambda m: _api_error(403, "PERMISSION_DENIED"))
    with pytest.raises(LLMAuthError):
        provider.chat([{"role": "user", "content": "hi"}])
    assert provider._client.models.calls == [provider.model]


def test_no_fallback_configured(monkeypatch):
    provider = _provider(monkeypatch, lambda m: _api_error(503, "UNAVAILABLE"), fallback="")
    with pytest.raises(LLMUnavailableError):
        provider.chat([{"role": "user", "content": "hi"}])
    assert set(provider._client.models.calls) == {provider.model}
