"""Qwen on Vertex AI: URLs, auth, JSON replies, typed errors, provider switch, and a whole interview on a fake endpoint."""
import json

import httpx
import pytest

from tests.test_interview_flow import AuthClient, _upload, _voice  # noqa: F401  (also sets the test env)

from app.config import settings
from app.providers import ollama_provider, vertex_provider
from app.providers.llm_errors import (
    LLMAuthError, LLMConfigError, LLMModelNotFoundError, LLMRateLimitError, LLMResponseError,
)

SCHEMA = {"type": "object", "properties": {"spoken_text": {"type": "string"}}, "required": ["spoken_text"]}


@pytest.fixture(autouse=True)
def vertex_settings(monkeypatch):
    for name, value in {
        "LLM_PROVIDER": "vertex", "VERTEX_PROJECT_ID": "my-proj", "VERTEX_LOCATION": "us-central1",
        "VERTEX_MODEL": "qwen/qwen-test-maas", "VERTEX_ENDPOINT_ID": "", "VERTEX_BASE_URL": "",
        "VERTEX_JSON_MODE": "json_object", "LLM_MODEL": "", "LLM_MAX_RETRIES": 1, "LLM_RETRY_BACKOFF_SECONDS": 0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    ollama_provider.reset_llm()
    yield
    ollama_provider.reset_llm()


def _provider(handler):
    provider = vertex_provider.VertexProvider()
    provider._http = httpx.Client(transport=httpx.MockTransport(handler))
    provider._token = lambda: "test-token"
    return provider


def _reply(content, **extra):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content},
                                                  "finish_reason": "stop"}], "usage": {}, **extra})


def test_managed_url_token_and_json_request():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _reply('```json\n{"spoken_text": "Hello Ravi"}\n```')

    result = _provider(handler).chat_json([{"role": "user", "content": "hi"}], SCHEMA)
    assert result == {"spoken_text": "Hello Ravi"}
    assert seen["url"] == ("https://us-central1-aiplatform.googleapis.com/v1/projects/my-proj/locations/"
                           "us-central1/endpoints/openapi/chat/completions")
    assert seen["auth"] == "Bearer test-token"
    body = seen["body"]
    assert body["model"] == "qwen/qwen-test-maas" and body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system" and "spoken_text" in body["messages"][0]["content"]


def test_dedicated_endpoint_global_location_and_base_url_override(monkeypatch):
    monkeypatch.setattr(settings, "VERTEX_ENDPOINT_ID", "12345")
    assert vertex_provider.build_base_url("p").endswith("/locations/us-central1/endpoints/12345")
    monkeypatch.setattr(settings, "VERTEX_ENDPOINT_ID", "")
    monkeypatch.setattr(settings, "VERTEX_LOCATION", "global")
    assert vertex_provider.build_base_url("p").startswith("https://aiplatform.googleapis.com/v1/projects/p/")
    monkeypatch.setattr(settings, "VERTEX_BASE_URL", "https://example.test/v1/")
    assert vertex_provider.build_base_url("p") == "https://example.test/v1"


def test_json_schema_and_plain_modes(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return _reply('{"spoken_text": "x"}')

    monkeypatch.setattr(settings, "VERTEX_JSON_MODE", "json_schema")
    _provider(handler).chat_json([{"role": "user", "content": "hi"}], SCHEMA)
    assert bodies[-1]["response_format"]["type"] == "json_schema"
    monkeypatch.setattr(settings, "VERTEX_JSON_MODE", "none")
    _provider(handler).chat_json([{"role": "user", "content": "hi"}], SCHEMA)
    assert "response_format" not in bodies[-1]
    _provider(handler).chat([{"role": "user", "content": "hi"}])
    assert all(m["role"] != "system" for m in bodies[-1]["messages"])      # plain chat: no schema note


def test_errors_are_typed_and_explain_the_fix():
    with pytest.raises(LLMAuthError) as exc:
        _provider(lambda r: httpx.Response(403, json={"error": "denied"})).chat([{"role": "user", "content": "x"}])
    assert "Vertex AI User" in exc.value.message
    with pytest.raises(LLMModelNotFoundError):
        _provider(lambda r: httpx.Response(404, text="nope")).chat([{"role": "user", "content": "x"}])
    with pytest.raises(LLMRateLimitError):
        _provider(lambda r: httpx.Response(429, text="slow down")).chat([{"role": "user", "content": "x"}])
    with pytest.raises(LLMResponseError):
        _provider(lambda r: _reply("not json at all")).chat_json([{"role": "user", "content": "x"}], SCHEMA)


def test_transient_errors_are_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="busy") if len(calls) == 1 else _reply("OK")

    assert _provider(handler).chat([{"role": "user", "content": "x"}]) == "OK"
    assert len(calls) == 2


def test_missing_model_is_a_config_error(monkeypatch):
    monkeypatch.setattr(settings, "VERTEX_MODEL", "")
    with pytest.raises(LLMConfigError):
        vertex_provider.VertexProvider()


def test_get_llm_follows_llm_provider(monkeypatch):
    assert isinstance(ollama_provider.get_llm(), vertex_provider.VertexProvider)
    ollama_provider.reset_llm()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    assert isinstance(ollama_provider.get_llm(), ollama_provider.OllamaProvider)
    assert settings.active_model == settings.OLLAMA_MODEL
    monkeypatch.setattr(settings, "LLM_PROVIDER", "vertex")
    assert settings.active_model == "qwen/qwen-test-maas"


def test_whole_interview_runs_on_a_fake_vertex_endpoint(monkeypatch):
    from app.services import interview_service, session_manager, voice_service
    requests = []

    def endpoint(request):
        payload = json.loads(request.content)
        requests.append(payload)
        system = payload["messages"][0]["content"]
        if "roles" in system:
            body = {"summary": "Support executive", "total_experience": "2 years",
                    "roles": [{"title": "Customer Support Executive", "company": "Acme Telecom",
                               "duration": "Jan 2022 - Present", "responsibilities": ["Handled inbound calls"]}],
                    "skills": ["CRM"], "tools_and_systems": ["Zendesk"]}
        elif "spoken_text" in system:
            n = len([m for m in payload["messages"] if m["role"] == "user"])
            body = {"learned": f"fact {n}", "topic": f"topic {n}", "move": "follow_up",
                    "end_interview": False, "spoken_text": f"Vertex question {n}?"}
        else:
            body = {"overview": "o", "topics_discussed": ["t"], "stated_experience": ["e"]}
        return _reply(json.dumps(body))

    provider = _provider(endpoint)
    monkeypatch.setattr(session_manager, "get_llm", lambda: provider)
    monkeypatch.setattr(interview_service, "get_llm", lambda: provider)
    monkeypatch.setattr(voice_service, "transcribe", lambda data, suffix=".wav": "I handled inbound billing calls.")
    monkeypatch.setattr(voice_service, "synthesize", lambda text: b"RIFFfakewav" + text.encode())
    from app.main import app
    with AuthClient(app) as client:
        candidate = _upload(client).json()["candidate_id"]
        started = client.post("/session/start", json={"candidate_id": candidate})
        assert started.status_code == 200, started.text
        sid = started.json()["session_id"]
        assert _voice(client, sid).status_code == 200
        assert client.post(f"/session/{sid}/end", json={"reason": "candidate_ended"}).status_code == 200
        transcript = client.get(f"/session/{sid}/result").json()["transcript"]
        assert any(t["role"] == "assistant" and t["text"].startswith("Vertex question") for t in transcript)
        health = client.get("/health").json()
        assert health["llm_provider"] == "vertex" and health["llm_model"] == "qwen/qwen-test-maas"
    assert requests and all(r["model"] == "qwen/qwen-test-maas" for r in requests)


def test_missing_google_credentials_give_a_clear_auth_error(monkeypatch):
    import google.auth

    def no_credentials(*args, **kwargs):
        raise google.auth.exceptions.DefaultCredentialsError("no credentials here")

    monkeypatch.setattr(google.auth, "default", no_credentials)
    with pytest.raises(LLMAuthError) as exc:
        vertex_provider._Credentials().token()
    assert "service account" in exc.value.message
