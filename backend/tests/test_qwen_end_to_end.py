"""
Whole turn-based interview through the real API with the model served by a FAKE Ollama server:

    browser audio -> /session/voice-answer -> (Whisper faked) -> OllamaProvider -> Ollama HTTP /api/chat -> (Piper faked)

Only the Ollama server, Whisper and Piper are faked; the provider, prompts, schemas, session logic and API are real.
"""
import json

import httpx
import pytest

from tests.test_interview_flow import AuthClient, RESUME, _upload, _voice  # noqa: F401  (also sets the test env)

from app.config import settings
from app.providers import ollama_provider


class FakeOllama:
    def __init__(self):
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:3b-instruct"}]})
        payload = json.loads(request.content)
        self.requests.append(payload)
        schema = payload.get("format") or {}
        props = schema.get("properties", {})
        if "roles" in props:                                   # resume analysis
            body = {"summary": "Support executive", "total_experience": "2 years",
                    "roles": [{"title": "Customer Support Executive", "company": "Acme Telecom",
                               "duration": "Jan 2022 - Present",
                               "responsibilities": ["Handled inbound voice calls for a US telecom account"]}],
                    "skills": ["Voice process", "CRM", "Zendesk"], "tools_and_systems": ["Zendesk"]}
        elif "spoken_text" in props:                           # interview turn
            n = len([m for m in payload["messages"] if m["role"] == "user"])
            body = {"learned": f"fact {n}", "topic": f"topic {n}", "move": "follow_up",
                    "end_interview": False, "spoken_text": f"Qwen question {n}?"}
        else:
            body = {"overview": "o", "topics_discussed": ["t"], "stated_experience": ["e"]}
        return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(body)},
                                         "done": True, "done_reason": "stop"})


@pytest.fixture()
def qwen(monkeypatch):
    from app.services import interview_service, session_manager, voice_service
    fake = FakeOllama()
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen2.5:3b-instruct")
    provider = ollama_provider.OllamaProvider()
    provider._http = httpx.Client(transport=httpx.MockTransport(fake))
    monkeypatch.setattr(session_manager, "get_llm", lambda: provider)
    monkeypatch.setattr(interview_service, "get_llm", lambda: provider)
    monkeypatch.setattr(voice_service, "transcribe", lambda data, suffix=".wav": "I handled inbound billing calls.")
    monkeypatch.setattr(voice_service, "synthesize", lambda text: b"RIFFfakewav" + text.encode())
    from app.main import app
    with AuthClient(app) as client:
        yield client, fake


def test_public_config_and_health_describe_the_qwen_setup(qwen):
    client, _ = qwen
    assert "voice_mode" not in client.get("/config/public").json()
    health = client.get("/health").json()
    assert health["llm_provider"] == "ollama" and health["llm_model"] == "qwen2.5:3b-instruct"
    assert client.post("/session/start-live", json={"candidate_id": 1}).status_code in (404, 405)


def test_a_full_interview_runs_on_qwen(qwen):
    client, fake = qwen
    candidate = _upload(client).json()["candidate_id"]

    started = client.post("/session/start", json={"candidate_id": candidate})
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    assert started.json().get("audio_base64")                 # the greeting is spoken (Piper stand-in)

    for _ in range(2):
        reply = _voice(client, sid)
        assert reply.status_code == 200, reply.text
        assert reply.json()["audio_base64"]
    assert client.post(f"/session/{sid}/end", json={"reason": "candidate_ended"}).status_code == 200

    transcript = client.get(f"/session/{sid}/result").json()["transcript"]
    spoken = [t["text"] for t in transcript if t["role"] == "assistant"]
    assert spoken and all(text.startswith("Qwen question") for text in spoken)   # words came from the model, not canned text
    assert any(t["role"] == "candidate" and "billing" in t["text"] for t in transcript)

    turn_calls = [r for r in fake.requests if "spoken_text" in (r.get("format") or {}).get("properties", {})]
    assert turn_calls and all(r["model"] == "qwen2.5:3b-instruct" for r in turn_calls)
    assert any("roles" in (r.get("format") or {}).get("properties", {}) for r in fake.requests)  # resume was analysed by Qwen


def test_model_down_returns_a_clear_503_not_a_fake_answer(qwen, monkeypatch):
    client, fake = qwen

    def down(request):
        raise httpx.ConnectError("refused")

    from app.services import session_manager
    broken = ollama_provider.OllamaProvider()
    broken._http = httpx.Client(transport=httpx.MockTransport(down))
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 0)
    monkeypatch.setattr(session_manager, "get_llm", lambda: broken)
    candidate = _upload(client).json()["candidate_id"]
    response = client.post("/session/start", json={"candidate_id": candidate})
    assert response.status_code == 503
    assert "ollama" in response.json()["detail"].lower()
