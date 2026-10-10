"""Gemini Live interview: WebSocket bridge tested against a FAKE Live session (no Google access needed)."""
import asyncio
import contextlib
import secrets
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from app.config import settings
from app.db.database import SessionLocal, init_db
from app.db.models import Candidate, Interview
from app.main import app
from app.services import live_service, session_manager
from app.services.interview_service import InterviewSession, InterviewSettings


def server_msg(**content):
    return types.LiveServerMessage(server_content=types.LiveServerContent(**content))


class FakeLive:
    """Plays a script of Gemini messages, one per receive() round, and records what the backend sent."""

    def __init__(self, script):
        self.script, self.sent_audio, self.sent_text, self.tool_responses = list(script), [], [], []

    async def receive(self):
        if not self.script:
            await asyncio.sleep(3600)        # idle until cancelled
        for message in self.script.pop(0):
            yield message

    async def send_realtime_input(self, audio=None, text=None, **_):
        if audio is not None:
            self.sent_audio.append((audio.mime_type, audio.data))
        if text is not None:
            self.sent_text.append(text)

    async def send_tool_response(self, function_responses=None, **_):
        self.tool_responses.extend(function_responses or [])


@pytest.fixture()
def live_env(monkeypatch):
    init_db()
    monkeypatch.setattr(settings, "INTERVIEW_MODE", "live")
    monkeypatch.setattr(settings, "MIN_TURNS_BEFORE_END", 1)
    db = SessionLocal()
    cand = Candidate(name="Ravi", interview_status="in_progress", invite_token=secrets.token_urlsafe(24))
    db.add(cand)
    db.commit()
    interview = Interview(candidate_id=cand.id, status="running", transcript=[], started_at=datetime.utcnow(),
                          access_token=secrets.token_urlsafe(24), settings_snapshot={})
    db.add(interview)
    db.commit()
    iid, token = interview.id, interview.access_token
    db.close()
    session = InterviewSession(InterviewSettings.from_config(), "Ravi", None, None, llm=object())
    monkeypatch.setitem(session_manager.SESSIONS, iid, session)

    holder = {}

    def install(script):
        fake = FakeLive(script)
        holder["fake"] = fake

        @contextlib.asynccontextmanager
        async def connect(_session):
            yield fake

        monkeypatch.setattr(live_service, "connect_live", connect)
        monkeypatch.setattr(live_service.evaluation_service, "evaluate_in_background", lambda _id: None)
        return fake

    return iid, token, install


def test_live_interview_streams_audio_saves_transcript_and_ends(live_env):
    iid, token, install = live_env
    fake = install([
        [server_msg(output_transcription=types.Transcription(text="Hi Ravi, tell me about yourself.")),
         server_msg(model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
             data=b"\x01\x02" * 100, mime_type="audio/pcm;rate=24000"))])),
         server_msg(turn_complete=True)],
        [server_msg(input_transcription=types.Transcription(text="I worked in ")),
         server_msg(input_transcription=types.Transcription(text="customer support.")),
         server_msg(output_transcription=types.Transcription(text="Thank you, goodbye.")),
         server_msg(turn_complete=True)],
        [types.LiveServerMessage(tool_call=types.LiveServerToolCall(
            function_calls=[types.FunctionCall(id="c1", name="end_interview")])),
         server_msg(turn_complete=True)],
    ])
    client = TestClient(app)
    with client.websocket_connect(f"/session/{iid}/live") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json() == {"type": "ready"}
        ws.send_bytes(b"\x00\x01" * 160)                      # one microphone frame
        events, audio = [], 0
        while True:
            message = ws.receive()
            if message.get("bytes") is not None:
                audio += len(message["bytes"])
                continue
            import json
            body = json.loads(message["text"])
            events.append(body["type"])
            if body["type"] == "finished":
                assert body["reason"] == "completed"
                break
    assert audio == 200 and "turn_complete" in events
    assert fake.sent_audio and fake.sent_audio[0][0] == "audio/pcm;rate=16000"
    assert "Begin the interview" in fake.sent_text[0]
    assert fake.tool_responses[0].response == {"result": "ok"}

    db = SessionLocal()
    interview = db.get(Interview, iid)
    assert interview.status == "finished" and interview.end_reason == "completed"
    roles = [(t["role"], t["text"]) for t in interview.transcript]
    assert roles == [("assistant", "Hi Ravi, tell me about yourself."),
                     ("candidate", "I worked in customer support."),
                     ("assistant", "Thank you, goodbye.")]
    db.close()


def test_candidate_can_end_the_interview(live_env):
    iid, token, install = live_env
    install([[]])
    with TestClient(app).websocket_connect(f"/session/{iid}/live") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "end"})
        assert ws.receive_json() == {"type": "finished", "reason": "candidate_ended"}
    db = SessionLocal()
    assert db.get(Interview, iid).status == "ended_early"
    db.close()


def test_early_end_interview_call_is_refused(live_env, monkeypatch):
    iid, token, install = live_env
    monkeypatch.setattr(settings, "MIN_TURNS_BEFORE_END", 4)
    fake = install([[types.LiveServerMessage(tool_call=types.LiveServerToolCall(
        function_calls=[types.FunctionCall(id="c1", name="end_interview")])), server_msg(turn_complete=True)]])
    with TestClient(app).websocket_connect(f"/session/{iid}/live") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.receive_json()
        assert ws.receive_json() == {"type": "turn_complete"}      # the interview did NOT end
        ws.send_json({"type": "end"})
        ws.receive_json()
    assert "not yet" in fake.tool_responses[0].response["result"]


def test_live_socket_requires_the_session_token(live_env):
    iid, token, install = live_env
    install([[]])
    client = TestClient(app)
    for first in ({"type": "auth", "token": "wrong"}, {"type": "hello"}):
        with pytest.raises(Exception):
            with client.websocket_connect(f"/session/{iid}/live") as ws:
                ws.send_json(first)
                ws.receive_json()
    with pytest.raises(Exception):                                  # wrong browser origin
        with client.websocket_connect(f"/session/{iid}/live", headers={"origin": "https://evil.example"}) as ws:
            ws.send_json({"type": "auth", "token": token})
            ws.receive_json()


def test_start_in_live_mode_needs_no_model_call(live_env, monkeypatch):
    iid, token, install = live_env
    db = SessionLocal()
    cand = Candidate(name="Asha", interview_status="invited", interview_attempts=0, invite_token=secrets.token_urlsafe(24),
                     resume_profile={"summary": "x"})
    db.add(cand)
    db.commit()
    cid, invite = cand.id, cand.invite_token
    db.close()
    from app.providers import factory
    monkeypatch.setattr(factory, "_provider", object())            # no model is needed to start a live interview
    r = TestClient(app).post("/session/start", json={"candidate_id": cid, "invite_token": invite})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "live" and body["session_token"] and "audio_base64" not in body


def test_live_instruction_drops_the_json_contract_and_adds_voice_rules():
    session = InterviewSession(InterviewSettings.from_config(), "Ravi", None, None, llm=object())
    text = live_service.live_instruction(session)
    assert "reply with ONE JSON object" not in text and "learned: short facts" not in text
    assert "end_interview function" in text and "CANDIDATE NAME: Ravi" in text


def test_live_interview_closes_when_the_tab_switch_limit_ends_it_from_outside(live_env):
    """The tab-switch rule runs on a normal REST call (maybe on another instance): the open socket must notice."""
    import json
    iid, token, install = live_env
    install([[server_msg(turn_complete=True)]])
    client = TestClient(app)
    with client.websocket_connect(f"/session/{iid}/live") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json() == {"type": "ready"}
        for _ in range(3):
            res = client.post(f"/session/{iid}/violation", headers={"X-Session-Token": token}, json={"type": "tab_switch"})
        assert res.json()["ended"] is True
        reason = None
        for _ in range(20):
            message = ws.receive()
            if message.get("text"):
                body = json.loads(message["text"])
                if body["type"] == "finished":
                    reason = body["reason"]
                    break
        assert reason == "tab_switch_limit"
    db = SessionLocal()
    try:
        assert db.get(Interview, iid).end_reason == "tab_switch_limit"
    finally:
        db.close()


def test_interviewer_can_pop_up_an_email_exercise_and_gets_the_work_back(live_env, monkeypatch):
    """give_exercise -> the browser gets the task, saves its email over REST, says exercise_done -> the model is told."""
    import json
    from app.services import chat_service
    iid, token, install = live_env
    monkeypatch.setattr(settings, "LIVE_EXERCISES", 2)
    task = {"id": "t1", "kind": "email", "title": "Refund request", "scenario": "A customer wants a refund.",
            "instructions": "Reply politely."}
    monkeypatch.setattr(chat_service, "generate_tasks", lambda job, cand, focus=None, count=None: [task])
    fake = install([[types.LiveServerMessage(tool_call=types.LiveServerToolCall(
        function_calls=[types.FunctionCall(id="x1", name="give_exercise", args={"kind": "email"})])),
        server_msg(turn_complete=True)]])
    client = TestClient(app)
    with client.websocket_connect(f"/session/{iid}/live") as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json() == {"type": "ready"}
        exercise = None
        for _ in range(10):
            message = ws.receive()
            if message.get("text"):
                body = json.loads(message["text"])
                if body["type"] == "exercise":
                    exercise = body["task"]
                    break
        assert exercise and exercise["id"] == "t1" and "customer_brief" not in exercise
        r = client.post(f"/chat/{iid}/email", headers={"X-Session-Token": token},
                        json={"task_id": "t1", "subject": "Your refund", "body": "Dear customer, we will refund you."})
        assert r.status_code == 200, r.text
        ws.send_json({"type": "exercise_done", "task_id": "t1"})
        for _ in range(50):
            if any("SYSTEM NOTE" in t and "we will refund you" in t for t in fake.sent_text):
                break
            import time
            time.sleep(0.05)
        ws.send_json({"type": "end"})
        for _ in range(40):                   # like the browser: wait for "finished" before closing the socket
            message = ws.receive()
            if message.get("text") and json.loads(message["text"]).get("type") == "finished":
                break
    assert any("SYSTEM NOTE" in t and "we will refund you" in t for t in fake.sent_text)
    assert fake.tool_responses and "email exercise" in fake.tool_responses[0].response["result"]
    import time
    for _ in range(200):                      # the interview closes asynchronously after "end"
        with SessionLocal() as check:
            closed = check.get(Interview, iid).status != "running"
        if closed:
            break
        time.sleep(0.05)
    assert closed
    assert client.post(f"/chat/{iid}/email", headers={"X-Session-Token": token},
                       json={"task_id": "t1", "subject": "x", "body": "y"}).status_code != 200   # interview ended
