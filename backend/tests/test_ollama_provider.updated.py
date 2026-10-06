"""Qwen through Ollama: structured replies, typed errors, provider switch, turn-only voice mode."""
import json

import httpx
import pytest

from app.config import settings
from app.providers import factory, ollama_provider
from app.providers.llm_errors import (
    LLMConnectionError, LLMModelNotFoundError, LLMResponseError, LLMUnavailableError,
)

SCHEMA = {"type": "object", "properties": {"spoken_text": {"type": "string"}}, "required": ["spoken_text"]}


def _provider(monkeypatch, handler):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "ENABLE_OLLAMA", True)
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen2.5:3b-instruct")
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_RETRY_BACKOFF_SECONDS", 0)
    provider = ollama_provider.OllamaProvider()
    provider._http = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def _reply(content, **extra):
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}, "done": True, **extra})


def test_structured_reply_is_parsed_and_the_schema_is_sent(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return _reply('{"spoken_text": "Hello, I am Alex."}')

    provider = _provider(monkeypatch, handler)
    out = provider.chat_json([{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}], SCHEMA)
    assert out == {"spoken_text": "Hello, I am Alex."}
    assert seen["model"] == "qwen2.5:3b-instruct" and seen["format"] == SCHEMA and seen["stream"] is False


def test_invalid_json_is_an_error_not_a_made_up_answer(monkeypatch):
    provider = _provider(monkeypatch, lambda r: _reply("not json at all"))
    with pytest.raises(LLMResponseError):
        provider.chat_json([{"role": "user", "content": "hi"}], SCHEMA)


def test_missing_model_says_how_to_install_it(monkeypatch):
    provider = _provider(monkeypatch, lambda r: httpx.Response(404, json={"error": "model 'x' not found"}))
    with pytest.raises(LLMModelNotFoundError) as info:
        provider.chat([{"role": "user", "content": "hi"}])
    assert "ollama pull" in info.value.message


def test_unreachable_ollama_is_a_connection_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    provider = _provider(monkeypatch, handler)
    with pytest.raises(LLMConnectionError):
        provider.chat([{"role": "user", "content": "hi"}])


def test_server_error_is_retried_then_reported(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, text="boom")

    provider = _provider(monkeypatch, handler)
    with pytest.raises(LLMUnavailableError):
        provider.chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 2            # 1 try + 1 retry


def test_ping_checks_the_model_is_installed(monkeypatch):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "other:latest"}]})
        return _reply("OK")

    provider = _provider(monkeypatch, handler)
    with pytest.raises(LLMModelNotFoundError):
        provider.ping()

    def good(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b-instruct"}]})
        return _reply("OK")

    provider = _provider(monkeypatch, good)
    assert provider.ping()["ok"] is True


def test_get_llm_returns_the_ollama_client(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "ENABLE_OLLAMA", True)
    factory.reset_llm()
    try:
        assert isinstance(factory.get_llm(), ollama_provider.OllamaProvider)
    finally:
        factory.reset_llm()


def test_active_model_prefers_the_override(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "ENABLE_OLLAMA", True)
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen2.5:3b-instruct")
    monkeypatch.setattr(settings, "LLM_MODEL", "")
    assert settings.active_model == "qwen2.5:3b-instruct"
    monkeypatch.setattr(settings, "LLM_MODEL", "qwen2.5:7b-instruct")
    assert settings.active_model == "qwen2.5:7b-instruct"
