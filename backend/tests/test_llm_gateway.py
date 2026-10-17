import json

import httpx
import pytest

from app.config import settings
from app.providers import gateway_provider as gp
from app.providers.gateway_provider import GatewayClient, RoutedLLM
from app.providers.llm_errors import (LLMAuthError, LLMConfigError, LLMError, LLMResponseError, LLMUnavailableError)

SCHEMA = {"type": "object", "properties": {"spoken_text": {"type": "string"}}, "required": ["spoken_text"]}
MSGS = [{"role": "user", "content": "hello"}]


class Primary:
    backend, model = "gemini", "gemini-x"

    def __init__(self, error=None, answer=None):
        self.error, self.answer, self.calls = error, answer or {"spoken_text": "from-vertex"}, 0

    def chat(self, messages, *, schema=None, temperature=None):
        self.calls += 1
        if self.error:
            raise self.error
        return json.dumps(self.answer)

    def chat_json(self, messages, schema, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        return self.answer

    def ping(self):
        return {"ok": True}


def gateway_client(status=200, body=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.read()) if request.method == "POST" else None)
            seen.append(dict(request.headers))
        return httpx.Response(status, json=body if body is not None else
                              {"text": '{"spoken_text":"from-gateway"}', "provider": "openai", "model": "m", "fallback": False})

    return GatewayClient("https://gw", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_gateway_unused_when_vertex_works():
    primary = Primary()
    r = RoutedLLM(primary, gateway_client(status=500), share=0)
    assert r.chat_json(MSGS, SCHEMA) == {"spoken_text": "from-vertex"} and primary.calls == 1


def test_vertex_overloaded_falls_back_to_gateway():
    r = RoutedLLM(Primary(error=LLMUnavailableError("busy")), gateway_client(), share=0)
    assert r.chat_json(MSGS, SCHEMA) == {"spoken_text": "from-gateway"}


def test_vertex_misconfigured_falls_back_to_gateway():
    for err in (LLMConfigError("no project"), LLMAuthError("denied")):
        assert RoutedLLM(Primary(error=err), gateway_client(), share=0).chat_json(MSGS, SCHEMA)["spoken_text"] == "from-gateway"


def test_safety_block_is_not_sent_to_another_model():
    blocked = LLMResponseError("blocked (SAFETY)", retryable=False)
    r = RoutedLLM(Primary(error=blocked), gateway_client(), share=0)
    with pytest.raises(LLMResponseError):
        r.chat_json(MSGS, SCHEMA)


def test_both_failing_reports_the_vertex_error():
    gw = gateway_client(status=503, body={"error": "x", "retryable": True, "attempts": [{"provider": "openai", "kind": "auth", "message": "m"}]})
    r = RoutedLLM(Primary(error=LLMUnavailableError("vertex down")), gw, share=0)
    with pytest.raises(LLMUnavailableError) as e:
        r.chat(MSGS)
    assert e.value.message == "vertex down"


def test_share_100_sends_to_gateway_first_and_vertex_is_the_fallback():
    primary = Primary()
    r = RoutedLLM(primary, gateway_client(), share=100)
    assert r.chat_json(MSGS, SCHEMA)["spoken_text"] == "from-gateway" and primary.calls == 0
    failing_gw = RoutedLLM(primary, gateway_client(status=503, body={"error": "x", "retryable": True, "attempts": [{"provider": "o", "kind": "unavailable"}]}), share=100)
    assert failing_gw.chat_json(MSGS, SCHEMA)["spoken_text"] == "from-vertex"


def test_share_splits_roughly_as_configured(monkeypatch):
    primary = Primary()
    r = RoutedLLM(primary, gateway_client(), share=50)
    seq = iter([10, 90] * 20)                       # below 50 -> gateway first, above -> vertex first
    monkeypatch.setattr(gp.random, "uniform", lambda a, b: next(seq))
    answers = [r.chat_json(MSGS, SCHEMA)["spoken_text"] for _ in range(40)]
    assert answers.count("from-gateway") == 20 and answers.count("from-vertex") == 20


def test_gateway_text_that_is_not_json_falls_back():
    gw = gateway_client(body={"text": "no json here", "provider": "openai", "model": "m", "fallback": False})
    r = RoutedLLM(Primary(), gw, share=100)
    assert r.chat_json(MSGS, SCHEMA)["spoken_text"] == "from-vertex"


def test_gateway_client_sends_token_and_schema(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_TOKEN", "tok")
    monkeypatch.setattr(settings, "LLM_GATEWAY_AUTH_MODE", "token")
    seen: list = []
    gateway_client(seen=seen).chat(MSGS, schema=SCHEMA, temperature=0.3, max_tokens=99)
    assert seen[0]["schema"] == SCHEMA and seen[0]["max_tokens"] == 99 and seen[0]["temperature"] == 0.3
    assert seen[1]["x-gateway-token"] == "tok"


@pytest.mark.parametrize("status,exc", [(401, LLMAuthError), (503, LLMConfigError)])
def test_gateway_errors_are_typed(status, exc):
    with pytest.raises(exc):
        gateway_client(status=status, body={"error": "x", "attempts": []}).chat(MSGS)


def test_factory_wraps_only_when_a_gateway_url_is_set(monkeypatch):
    from app.providers import factory
    factory.reset_llm()
    monkeypatch.setattr(settings, "LLM_GATEWAY_URL", "")
    monkeypatch.setattr(gp, "RoutedLLM", gp.RoutedLLM)
    import app.providers.gemini_provider as gem
    monkeypatch.setattr(gem, "GeminiProvider", lambda: Primary())
    assert isinstance(factory.get_llm(), Primary)
    factory.reset_llm()
    monkeypatch.setattr(settings, "LLM_GATEWAY_URL", "https://gw")
    assert isinstance(factory.get_llm(), RoutedLLM)
    factory.reset_llm()
