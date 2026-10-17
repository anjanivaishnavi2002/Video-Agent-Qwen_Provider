import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import ProviderError
from app.providers.gemini_api import GeminiApiProvider
from app.providers.openai_compat import OpenAICompatible
from app.router import AllProvidersFailed, NoProviders, Router, build_providers

SCHEMA = {"type": "object", "properties": {"spoken_text": {"type": "string"}}, "required": ["spoken_text"]}
MSGS = [{"role": "system", "content": "be brief"}, {"role": "assistant", "content": "hi"}, {"role": "user", "content": "yo"}]


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def cfg(**kw):
    base = dict(GATEWAY_RETRIES=0, GATEWAY_RETRY_BACKOFF_SECONDS=0, GATEWAY_COOLDOWN_SECONDS=60)
    base.update(kw)
    return Settings(**base)


# ---- individual providers ----------------------------------------------------------------------------------
def test_openai_plain_and_structured_payloads():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.read())
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"model": "gpt-x", "choices": [{"message": {"content": '{"spoken_text":"hello"}'},
                                                                         "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

    p = OpenAICompatible("openai", "sk-1", "gpt-x", "https://x/v1/chat/completions", token_param="max_completion_tokens",
                         http=client_for(handler))
    out = p.complete(MSGS, schema=SCHEMA, temperature=0.2, max_tokens=100, timeout=5)
    assert json.loads(out.text) == {"spoken_text": "hello"} and out.tokens_in == 5
    assert seen["auth"] == "Bearer sk-1" and seen["body"]["max_completion_tokens"] == 100
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"][0]["role"] == "system" and "JSON schema" in seen["body"]["messages"][0]["content"]


def test_openai_bad_json_is_a_retryable_bad_response():
    h = lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]})
    p = OpenAICompatible("openai", "k", "m", "https://x", token_param="max_tokens", http=client_for(h))
    with pytest.raises(ProviderError) as e:
        p.complete(MSGS, schema=SCHEMA, temperature=0.2, max_tokens=10, timeout=5)
    assert e.value.kind == "bad_response" and e.value.retryable


@pytest.mark.parametrize("status,kind,retry", [(401, "auth", False), (404, "model_not_found", False),
                                               (429, "rate_limit", True), (503, "unavailable", True)])
def test_http_errors_are_classified(status, kind, retry):
    p = OpenAICompatible("openai", "k", "m", "https://x", token_param="max_tokens",
                         http=client_for(lambda r: httpx.Response(status, text="boom")))
    with pytest.raises(ProviderError) as e:
        p.complete(MSGS, schema=None, temperature=0.2, max_tokens=10, timeout=5)
    assert e.value.kind == kind and e.value.retryable is retry


def test_anthropic_uses_forced_tool_for_structure_and_alternating_turns():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.read())
        seen["key"] = request.headers["x-api-key"]
        return httpx.Response(200, json={"model": "claude-x", "stop_reason": "tool_use",
                                         "content": [{"type": "tool_use", "name": "respond", "input": {"spoken_text": "hey"}}],
                                         "usage": {"input_tokens": 4, "output_tokens": 2}})

    p = AnthropicProvider("ak", "claude-x", http=client_for(handler))
    out = p.complete(MSGS, schema=SCHEMA, temperature=0.5, max_tokens=50, timeout=5)
    assert json.loads(out.text) == {"spoken_text": "hey"} and seen["key"] == "ak"
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "respond"}
    assert seen["body"]["system"] == "be brief"
    roles = [m["role"] for m in seen["body"]["messages"]]
    assert roles[0] == "user" and all(a != b for a, b in zip(roles, roles[1:]))   # starts with user, alternates


def test_gemini_api_structured_and_blocked():
    seen = {}

    def ok(request):
        seen["key"] = request.headers["x-goog-api-key"]
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "thinking...", "thought": True}, {"text": '{"spoken_text":"hi"}'}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2}})

    p = GeminiApiProvider("gk", "gemini-x", http=client_for(ok))
    out = p.complete(MSGS, schema=SCHEMA, temperature=0.1, max_tokens=50, timeout=5)
    assert json.loads(out.text) == {"spoken_text": "hi"} and seen["key"] == "gk"
    assert seen["body"]["generationConfig"]["responseJsonSchema"] == SCHEMA
    blocked = GeminiApiProvider("gk", "m", http=client_for(
        lambda r: httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]})))
    with pytest.raises(ProviderError) as e:
        blocked.complete(MSGS, schema=None, temperature=0.1, max_tokens=50, timeout=5)
    assert e.value.kind == "blocked"


# ---- router ------------------------------------------------------------------------------------------------
class Fake:
    def __init__(self, name, fail=None, calls=None):
        self.name, self.model, self.fail, self.calls = name, name + "-m", fail, calls if calls is not None else []

    def complete(self, messages, **kw):
        self.calls.append(self.name)
        if self.fail:
            raise ProviderError("down", kind=self.fail, retryable=self.fail in ("unavailable", "rate_limit"))
        from app.providers.base import Completion
        return Completion("ok-" + self.name, self.name + "-m")


def router_of(providers, **kw):
    return Router({p.name: p for p in providers}, cfg(**kw))


def test_requests_are_spread_in_proportion_to_weights():
    a, b = Fake("a"), Fake("b")
    r = router_of([a, b], GATEWAY_WEIGHTS="a:3,b:1")
    for _ in range(40):
        r.complete([{"role": "user", "content": "x"}], schema=None, temperature=0.5, max_tokens=10)
    assert len(a.calls) == 30 and len(b.calls) == 10


def test_failover_and_cooldown_then_recovery_path():
    a, b = Fake("a", fail="unavailable"), Fake("b")
    r = router_of([a, b], GATEWAY_WEIGHTS="a:1,b:1")
    res = r.complete([{"role": "user", "content": "x"}], schema=None, temperature=0.5, max_tokens=10)
    assert res.provider == "b" and res.fallback in (True, False)
    assert r.in_cooldown("a")                      # a failed, so it is skipped from now on
    a.calls.clear()
    for _ in range(5):
        r.complete([{"role": "user", "content": "x"}], schema=None, temperature=0.5, max_tokens=10)
    assert a.calls == []                          # cooling down: not even tried


def test_all_failing_raises_with_attempts_and_no_content():
    r = router_of([Fake("a", fail="auth"), Fake("b", fail="unavailable")])
    with pytest.raises(AllProvidersFailed) as e:
        r.complete([{"role": "user", "content": "SECRET"}], schema=None, temperature=0.5, max_tokens=10)
    assert {a.provider for a in e.value.attempts} == {"a", "b"} and "SECRET" not in str(e.value.attempts)


def test_safety_block_is_not_shopped_around():
    a, b = Fake("a", fail="blocked"), Fake("b")
    r = router_of([a, b], GATEWAY_WEIGHTS="a:1,b:0")
    with pytest.raises(AllProvidersFailed):
        r.complete([{"role": "user", "content": "x"}], schema=None, temperature=0.5, max_tokens=10)
    assert b.calls == []


def test_no_providers_and_exclude():
    with pytest.raises(NoProviders):
        router_of([]).complete([{"role": "user", "content": "x"}], schema=None, temperature=0.5, max_tokens=10)
    built = build_providers(Settings(OPENAI_API_KEY="k", DEEPSEEK_API_KEY="k", GATEWAY_EXCLUDE="deepseek"))
    assert list(built) == ["openai"]


# ---- HTTP API ----------------------------------------------------------------------------------------------
@pytest.fixture()
def api(monkeypatch):
    import app.main as main
    fake = Fake("a")
    monkeypatch.setattr(main, "router", lambda: router_of([fake]))
    return TestClient(main.app), fake


def test_api_requires_token_and_answers(api):
    client, _ = api
    body = {"messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat", json=body).status_code == 401
    ok = client.post("/v1/chat", json=body, headers={"X-Gateway-Token": "test-token"})
    assert ok.status_code == 200 and ok.json()["text"] == "ok-a" and ok.json()["provider"] == "a"
    assert client.get("/health").status_code == 200


def test_api_503_without_providers_and_schema_alias(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "router", lambda: router_of([]))
    c = TestClient(main.app)
    r = c.post("/v1/chat", json={"messages": [{"role": "user", "content": "hi"}], "schema": SCHEMA},
               headers={"X-Gateway-Token": "test-token"})
    assert r.status_code == 503 and "configured" in r.json()["error"]
    assert c.get("/ready").status_code == 503
