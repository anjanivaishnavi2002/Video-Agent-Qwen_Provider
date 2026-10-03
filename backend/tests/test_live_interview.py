"""
Live (real-time voice) interview: browser <-> FastAPI <-> Gemini Live.

The fake Live connection behaves like the real SDK: receive() stops after every model turn,
so these tests fail if the relay does not keep re-entering it.

    python -m pytest tests/test_live_interview.py -q
"""
import asyncio

import pytest
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from tests.test_interview_flow import RESUME, _upload, env  # noqa: F401  (env = shared fixture)


# ----------------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------------

def _turn(assistant, candidate=None, *, tool=None):
    """The messages Gemini sends for one model turn."""
    msgs = []
    if tool:
        msgs.append(types.LiveServerMessage(
            tool_call=types.LiveServerToolCall(
                function_calls=[types.FunctionCall(id=f"call-{tool}", name=tool, args={})])))
    msgs.append(types.LiveServerMessage(server_content=types.LiveServerContent(
        input_transcription=types.Transcription(text=candidate) if candidate else None,
        output_transcription=types.Transcription(text=assistant),
        model_turn=types.Content(role="model", parts=[types.Part(
            inline_data=types.Blob(data=b"pcm24k", mime_type="audio/pcm;rate=24000"))]),
    )))
    msgs.append(types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True)))
    return msgs


class SdkLikeLive:
    """One Live connection. `script` = list of turns; turn 0 starts on the greeting text,
    every later turn starts when an audio frame arrives (one frame = one candidate answer)."""

    def __init__(self, script, extra_after_first=None):
        self.script = list(script)
        self.queue = asyncio.Queue()
        self.texts, self.audio, self.tool_responses = [], [], []
        self.started = False
        self.extra_after_first = extra_after_first or []

    def _next(self):
        if self.script:
            for msg in self.script.pop(0):
                self.queue.put_nowait(msg)

    async def send_realtime_input(self, *, text=None, audio=None, **_):
        if text is not None:
            self.texts.append(text)
            if not self.started:
                self.started = True
                self._next()
                for msg in self.extra_after_first:
                    self.queue.put_nowait(msg)
            elif "Note to the interviewer" in text:
                self._next()               # the model reacts to a system note with a turn
        if audio is not None:
            self.audio.append(audio.data)
            self._next()

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)

    async def receive(self):               # same contract as the real SDK: ends after each turn
        while True:
            msg = await self.queue.get()
            yield msg
            if msg.server_content and msg.server_content.turn_complete:
                return


class FakeClient:
    def __init__(self, connections=None, error=None):
        self.connections = list(connections or [])
        self.opened, self.configs, self.models = [], [], []
        self.error = error
        client = self

        class Ctx:
            def __init__(self, live):
                self.live = live

            async def __aenter__(self):
                return self.live

            async def __aexit__(self, *_):
                return None

        def connect(*, model, config):
            if client.error:
                raise client.error
            client.models.append(model)
            client.configs.append(config)
            live = client.connections.pop(0)
            client.opened.append(live)
            return Ctx(live)

        self.aio = type("Aio", (), {"live": type("L", (), {"connect": staticmethod(connect)})()})()


def _start(client):
    candidate = _upload(client).json()
    started = client.post("/session/start-live", json={"candidate_id": candidate["candidate_id"]}).json()
    return started["session_id"], started["session_token"]


def _recv_until(socket, wanted, limit=40):
    seen = []
    for _ in range(limit):
        item = socket.receive_json()
        seen.append(item)
        if item["type"] in wanted:
            return seen
    raise AssertionError(f"never saw {wanted}; got {[i['type'] for i in seen]}")


@pytest.fixture
def patched(env, monkeypatch):  # noqa: F811
    from app.config import settings
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LIVE_SILENCE_CHECKIN_SECONDS", 0)

    def install(fake_client):
        monkeypatch.setattr(genai, "Client", lambda **kw: fake_client)
        return fake_client

    return env, install, settings, monkeypatch


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------

def test_conversation_continues_across_turns_and_finishes(patched):
    (client, _, _, manager), install, settings, _ = patched
    sid, token = _start(client)
    live = SdkLikeLive([
        _turn("Hi Ravi, tell me about yourself?"),
        _turn("Which CRM did you use?", "I did voice support."),
        _turn("How did you handle billing disputes?", "Mostly Zendesk."),
        _turn("Thank you, the team will be in touch.", "I stayed calm and checked the policy.", tool="finish_interview"),
    ])
    fake = install(FakeClient([live]))

    with client.websocket_connect(f"/session/{sid}/live") as socket:
        socket.send_json({"session_token": token})
        assert socket.receive_json() == {"type": "ready", "model": "gemini-3.8-live"}
        _recv_until(socket, {"turn_complete"})                   # greeting
        for _ in range(2):                                       # two more full turns: proves it keeps listening
            socket.send_bytes(b"pcm16k")
            seen = _recv_until(socket, {"turn_complete"})
            assert any(i["type"] == "audio" for i in seen)
        socket.send_bytes(b"pcm16k")
        seen = _recv_until(socket, {"finished"})
        assert any(i["type"] == "audio" for i in seen)

    assert fake.models == ["gemini-3.8-live"]
    assert live.tool_responses[0].response == {"ok": True}
    from app.db.database import SessionLocal
    from app.db.models import Interview
    db = SessionLocal()
    try:
        interview = db.get(Interview, sid)
        assert interview.status == "finished" and interview.end_reason == "completed"
        roles = [t["role"] for t in interview.transcript]
        assert roles == ["assistant", "candidate", "assistant", "candidate", "assistant", "candidate", "assistant"]
        assert interview.transcript[1]["text"] == "I did voice support."
    finally:
        db.close()
    assert sid not in manager.SESSIONS


def test_model_cannot_finish_too_early(patched):
    (client, *_), install, settings, _ = patched          # MIN_TURNS_BEFORE_END=2 in the shared env
    sid, token = _start(client)
    live = SdkLikeLive([
        _turn("Hi Ravi, tell me about yourself?"),
        _turn("Thanks, goodbye!", "I did voice support.", tool="finish_interview"),   # after only 1 answer
        _turn("One more thing: which tools?", "Okay."),
    ])
    install(FakeClient([live]))
    with client.websocket_connect(f"/session/{sid}/live") as socket:
        socket.send_json({"session_token": token})
        _recv_until(socket, {"ready"})
        _recv_until(socket, {"turn_complete"})
        socket.send_bytes(b"x")
        seen = _recv_until(socket, {"turn_complete", "finished"})
        assert seen[-1]["type"] == "turn_complete"                 # the interview did NOT end
        socket.send_bytes(b"x")
        _recv_until(socket, {"turn_complete"})
        socket.send_json({"type": "end"})
        assert socket.receive_json() == {"type": "finished"}
    assert live.tool_responses[0].response["ok"] is False


def test_google_errors_are_reported_not_faked(patched):
    (client, *_), install, settings, _ = patched
    sid, token = _start(client)
    install(FakeClient(error=genai_errors.ClientError(
        404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})))
    with client.websocket_connect(f"/session/{sid}/live") as socket:
        socket.send_json({"session_token": token})
        message = socket.receive_json()
    assert message["type"] == "error" and "not found" in message["message"].lower()


def test_missing_api_key_is_a_clear_error(patched):
    (client, *_), _, settings, mp = patched
    sid, token = _start(client)
    mp.setattr(settings, "GEMINI_API_KEY", "")
    with client.websocket_connect(f"/session/{sid}/live") as socket:
        socket.send_json({"session_token": token})
        message = socket.receive_json()
    assert message["type"] == "error" and "GEMINI_API_KEY" in message["message"]


def test_session_resumption_bridges_the_connection_limit(patched):
    (client, *_), install, settings, _ = patched
    sid, token = _start(client)
    handle_msg = types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(new_handle="handle-1", resumable=True))
    go_away = types.LiveServerMessage(go_away=types.LiveServerGoAway())
    first = SdkLikeLive([_turn("Hi Ravi, tell me about yourself?")], extra_after_first=[handle_msg, go_away])
    second = SdkLikeLive([_turn("Welcome back to the call. Which CRM?", "I did voice support.")])
    second.started = True        # a resumed session must NOT be greeted again
    fake = install(FakeClient([first, second]))
    with client.websocket_connect(f"/session/{sid}/live") as socket:
        socket.send_json({"session_token": token})
        _recv_until(socket, {"ready"})
        _recv_until(socket, {"turn_complete"})
        import time
        for _ in range(100):                      # wait for the reconnect
            if len(fake.opened) == 2:
                break
            time.sleep(0.05)
        assert len(fake.opened) == 2
        socket.send_bytes(b"x")
        _recv_until(socket, {"turn_complete"})
        socket.send_json({"type": "end"})
        _recv_until(socket, {"finished"})
    assert fake.configs[0].session_resumption.handle is None
    assert fake.configs[1].session_resumption.handle == "handle-1"
    assert second.texts == []                       # no second greeting


def test_silent_candidate_is_checked_on_then_session_closes(patched):
    (client, _, _, manager), install, settings, mp = patched
    from app.services import live_service
    mp.setattr(live_service, "TICK_SECONDS", 0.05)
    mp.setattr(settings, "LIVE_SILENCE_CHECKIN_SECONDS", 0.3)
    sid, token = _start(client)
    manager.SESSIONS[sid].cfg.max_empty_streak = 2
    live = SdkLikeLive([
        _turn("Hi Ravi, tell me about yourself?"),
        _turn("Are you still there?"),
        _turn("I will end the session here, thank you.", tool="finish_interview"),
    ])
    install(FakeClient([live]))
    with client.websocket_connect(f"/session/{sid}/live") as socket:
        socket.send_json({"session_token": token})
        _recv_until(socket, {"ready"})
        _recv_until(socket, {"finished"}, limit=60)
    notes = [t for t in live.texts if "Note to the interviewer" in t]
    assert len(notes) == 2 and "silent" in notes[0]
    from app.db.database import SessionLocal
    from app.db.models import Interview
    db = SessionLocal()
    try:
        assert db.get(Interview, sid).end_reason == "unresponsive"
    finally:
        db.close()


def test_time_limit_wraps_up_then_hard_stops(patched):
    (client, _, _, manager), install, settings, mp = patched
    from app.services import live_service
    mp.setattr(live_service, "TICK_SECONDS", 0.05)
    mp.setattr(settings, "LIVE_WRAPUP_LEAD_SECONDS", 0)
    mp.setattr(settings, "LIVE_FORCE_END_GRACE_SECONDS", 0)
    sid, token = _start(client)
    manager.SESSIONS[sid].cfg.duration_minutes = 0.02          # ~1.2 s
    live = SdkLikeLive([_turn("Hi Ravi, tell me about yourself?")])
    install(FakeClient([live]))
    with client.websocket_connect(f"/session/{sid}/live") as socket:
        socket.send_json({"session_token": token})
        _recv_until(socket, {"ready"})
        _recv_until(socket, {"finished"}, limit=60)
    assert any("Time is almost up" in t for t in live.texts)
    from app.db.database import SessionLocal
    from app.db.models import Interview
    db = SessionLocal()
    try:
        assert db.get(Interview, sid).end_reason == "time_limit"
    finally:
        db.close()


def test_live_config_is_resume_driven_and_provider_aware(patched):
    (client, _, _, manager), _, settings, mp = patched
    sid, _ = _start(client)
    from app.providers import gemini_provider
    from app.providers.llm_errors import LLMConfigError
    from app.services.live_service import build_live_config

    config = build_live_config(manager.SESSIONS[sid], handle="abc")
    text = config.system_instruction
    assert "Ravi Kumar" in text and "finish_interview" in text and "OUTPUT FORMAT" not in text
    assert "Customer handling" in text           # BPO guidance comes from bpo_context.yaml, not from code
    assert config.realtime_input_config.automatic_activity_detection.silence_duration_ms == settings.LIVE_SILENCE_MS
    assert config.session_resumption.handle == "abc"
    assert config.tools[0].function_declarations[0].name == "finish_interview"
    assert list(config.response_modalities) == ["AUDIO"]

    # Production: Vertex AI with the VM's service account, no API key
    captured = {}
    mp.setattr(genai, "Client", lambda **kw: captured.update(kw) or object())
    mp.setattr(settings, "LLM_PROVIDER", "vertex")
    mp.setattr(settings, "VERTEX_PROJECT_ID", "my-project")
    mp.setattr(settings, "GEMINI_API_KEY", "")
    gemini_provider.build_client()
    assert captured["vertexai"] is True and captured["project"] == "my-project" and "api_key" not in captured
    assert settings.live_model == settings.VERTEX_LIVE_MODEL
    mp.setattr(settings, "VERTEX_PROJECT_ID", "")
    with pytest.raises(LLMConfigError):
        gemini_provider.build_client()


def test_health_reports_the_configured_model_without_secrets(patched):
    (client, *_), _, settings, _ = patched
    body = client.get("/health").json()
    assert body["llm_provider"] == "gemini" and body["llm_model"] == "gemini-3.8-flash"
    assert body["configured"] is True and "test-key" not in str(body)
