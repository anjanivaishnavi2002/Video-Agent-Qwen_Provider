"""
End-to-end API test with a FAKE LLM and FAKE Whisper/Piper (no models needed).

    pip install pytest
    python -m pytest tests -q
"""
import base64
import json
import re
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"
os.environ["MIN_TURNS_BEFORE_END"] = "2"
os.environ["MAX_TURNS"] = "5"
os.environ["EMPTY_BEFORE_REPROMPT"] = "2"
os.environ["MAX_EMPTY_STREAK"] = "4"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

RESUME = """Ravi Kumar
Customer Support Executive at Acme Telecom, Jan 2022 - Present (2 years)
- Handled inbound voice calls for a US telecom account using Zendesk CRM
- Resolved billing disputes and escalations, maintained CSAT above target
Skills: Voice process, CRM, English communication, Zendesk
Education: B.Com, Osmania University
""" * 2


class FakeLLM:
    """Records every prompt; answers with valid interviewer JSON that depends on the input."""

    def __init__(self):
        self.calls = []
        self.end_early = False

    def chat(self, messages, *, schema=None, temperature=None):
        self.calls.append(messages)
        last = messages[-1]["content"]
        if schema and "roles" in schema.get("properties", {}):  # resume analysis
            return json.dumps(
                {
                    "summary": "Support executive with voice experience",
                    "total_experience": "2 years",
                    "roles": [
                        {"title": "Customer Support Executive", "company": "Acme Telecom",
                         "duration": "Jan 2022 - Present",
                         "responsibilities": ["Handled inbound voice calls for a US telecom account"]},
                        {"title": "Pilot", "company": "Sky Air", "responsibilities": []},  # invented
                    ],
                    "skills": ["Voice process", "CRM", "Zendesk", "Kubernetes"],       # Kubernetes invented
                    "tools_and_systems": ["Zendesk"],
                }
            )
        if "Begin the interview" in last:
            return json.dumps({"learned": "", "topic": "introduction", "move": "opening",
                               "end_interview": False,
                               "spoken_text": "Hi Ravi, I'm Priya. Tell me about yourself."})
        if "Time or turn limit reached" in last:
            return json.dumps({"learned": "", "topic": "closing", "move": "closing",
                               "end_interview": True, "spoken_text": "Thanks, we will be in touch."})
        if "audio was silent" in last or "silent for a while" in last:
            return json.dumps({"learned": "", "topic": "clarify", "move": "clarify",
                               "end_interview": False, "spoken_text": "Sorry, I missed that. Could you repeat?"})
        if "has not responded several times" in last:
            return json.dumps({"learned": "", "topic": "closing", "move": "closing",
                               "end_interview": True, "spoken_text": "I'll end here. Thank you."})
        turn = len([m for m in messages if m["role"] == "user"])
        return json.dumps({"learned": f"fact {turn}", "topic": f"topic {turn}", "move": "follow_up",
                           "end_interview": self.end_early, "spoken_text": f"Question number {turn}?"})

    def chat_json(self, messages, schema, **kw):
        from app.providers.qwen_provider import parse_json_loosely
        return parse_json_loosely(self.chat(messages, schema=schema, **kw))


TOKENS: dict[int, str] = {}


class AuthClient(TestClient):
    """Behaves like the browser: remembers the session token and sends it on session calls."""

    def request(self, method, url, **kw):
        match = re.match(r"/session/(\d+)/", str(url))
        headers = dict(kw.get("headers") or {})
        anonymous = headers.pop("X-Anonymous", None)     # tests can opt out of auto-auth
        kw["headers"] = headers
        if match and int(match.group(1)) in TOKENS and not anonymous:
            headers.setdefault("X-Session-Token", TOKENS[int(match.group(1))])
        response = super().request(method, url, **kw)
        if str(url) == "/session/start" and response.status_code == 200:
            body = response.json()
            TOKENS[body["session_id"]] = body["session_token"]
        return response


@pytest.fixture(scope="module")
def env():
    from app.services import session_manager, voice_service, interview_service
    fake = FakeLLM()
    session_manager.get_llm = lambda: fake
    interview_service.get_llm = lambda: fake

    transcripts = iter([])
    state = {"texts": []}
    voice_service.transcribe = lambda data, suffix=".wav": state["texts"].pop(0) if state["texts"] else "I worked on voice support."
    voice_service.synthesize = lambda text: b"RIFFfakewav" + text.encode()

    from app.main import app
    with AuthClient(app) as client:
        yield client, fake, state, session_manager


def _upload(client, name="Ravi", text=RESUME, filename="cv.txt", consent=True):
    version = client.get("/config/consent").json()["version"] if consent else "wrong"
    return client.post("/resume/upload", data={"name": name, "consent_version": version},
                       files={"file": (filename, text.encode())})


def _voice(client, sid, data=b"audio"):
    return client.post(f"/session/{sid}/voice-answer", files={"audio": ("a.wav", data)})


def test_public_config_exposes_settings(env):
    client, *_ = env
    cfg = client.get("/config/public").json()
    assert cfg["voice"]["silence_ms"] == 1800 and ".pdf" in cfg["resume"]["allowed_extensions"]
    assert "job" not in json.dumps(cfg).lower()


def test_resume_validation(env):
    client, *_ = env
    assert _upload(client, filename="cv.exe").status_code == 400
    assert _upload(client, text="hi").status_code == 422          # too little text
    assert _upload(client, name="  ").status_code == 400


def test_full_interview(env):
    client, fake, state, sm = env
    cand = _upload(client).json()
    started = client.post("/session/start", json={"candidate_id": cand["candidate_id"]}).json()
    sid = started["session_id"]

    # audio only, no question text leaks to the client
    assert base64.b64decode(started["audio_base64"]).startswith(b"RIFF")
    assert "ai_text" not in started and "ai_message" not in started

    # system prompt is built from the resume + candidate, contains no job
    system = fake.calls[-1][0]["content"]
    assert "Ravi" in system and "Zendesk" in system and "Acme Telecom" in system
    assert "JOB" not in system.upper().replace("JOB-", "")
    # grounding removed the invented items
    profile = sm.SESSIONS[sid].system_prompt
    assert "Kubernetes" not in profile and "Pilot" not in profile and "Sky Air" not in profile

    # model tries to end on the very first answer -> engine forces a continuation
    fake.end_early = True
    r1 = _voice(client, sid).json()
    assert r1["status"] == "ok" and not r1["finished"] and "candidate_text" not in r1
    prompt = fake.calls[-1]
    assert "It is too early to end" in prompt[-1]["content"]
    assert "I worked on voice support." in prompt[-1]["content"]
    assert sum("I worked on voice support." in m["content"] for m in prompt) == 1  # answer not duplicated
    fake.end_early = False

    r2 = _voice(client, sid).json()
    assert not r2["finished"]

    # state carries topics + learned facts (anti-repetition memory)
    state_msg = fake.calls[-1][-1]["content"]
    assert "Topics already covered" in state_msg and "introduction" in state_msg
    assert "fact" in state_msg

    # turn limit (MAX_TURNS=5): answers 3,4 continue, 5 wraps up
    assert not _voice(client, sid).json()["finished"]
    assert not _voice(client, sid).json()["finished"]
    last = _voice(client, sid).json()
    assert last["finished"] is True
    result = client.get(f"/session/{sid}/result").json()
    assert result["status"] == "finished" and result["end_reason"] == "max_turns"
    assert result["transcript"][0]["role"] == "assistant"
    assert "job" not in result and result["settings"]["interviewer_name"]

    # finished session no longer accepts answers
    assert _voice(client, sid).status_code == 404


def test_empty_audio_and_silence_paths(env):
    client, fake, state, sm = env
    cand = _upload(client).json()
    sid = client.post("/session/start", json={"candidate_id": cand["candidate_id"]}).json()["session_id"]

    state["texts"] += ["", ""]                       # first empty -> keep listening
    assert _voice(client, sid).json() == {"status": "no_speech", "finished": False}
    r = _voice(client, sid).json()                   # second empty -> AI asks to repeat
    assert r["status"] == "ok" and "audio_base64" in r

    nr = client.post(f"/session/{sid}/no-response").json()  # long silence -> check-in
    assert nr["status"] == "ok" and not nr["finished"]

    nr = client.post(f"/session/{sid}/no-response").json()  # streak reaches MAX_EMPTY_STREAK
    assert nr["finished"] is True
    res = client.get(f"/session/{sid}/result").json()
    assert res["end_reason"] == "unresponsive" and res["status"] == "ended_early"


def test_restore_after_restart_events_and_video(env):
    client, fake, state, sm = env
    cand = _upload(client).json()
    sid = client.post("/session/start", json={"candidate_id": cand["candidate_id"]}).json()["session_id"]
    _voice(client, sid)

    sm.SESSIONS.clear()                              # simulate server restart
    r = _voice(client, sid).json()
    assert r["status"] == "ok"
    restored = sm.SESSIONS[sid]
    assert restored.turns == 2 and len(restored.transcript) == 5

    ev = client.post(f"/session/{sid}/events", json={"events": [
        {"type": "face_missing", "timestamp": "2026-01-01T10:00:00Z", "offset_ms": 4200},
        {"type": "multiple_faces", "offset_ms": 9000, "details": {"faces": 2}},
        {"type": "looks_nervous", "offset_ms": 1},          # inference -> must be rejected
    ]}).json()
    assert ev == {"stored": 2, "ignored": 1}

    up = client.post(f"/session/{sid}/video", files={"video": ("interview.webm", b"x" * 2048)})
    assert up.status_code == 200 and up.json()["size_bytes"] == 2048
    assert client.post(f"/session/{sid}/video", files={"video": ("a.exe", b"x")}).status_code == 400

    res = client.get(f"/session/{sid}/result").json()
    assert [e["type"] for e in res["events"]] == ["face_missing", "multiple_faces"]
    assert res["video_size_bytes"] == 2048 and res["events"][0]["offset_ms"] == 4200

    end = client.post(f"/session/{sid}/end", json={"reason": "candidate_ended"}).json()
    assert end["finished"] is True
    assert client.get(f"/session/{sid}/result").json()["status"] == "ended_early"


def test_long_resume_is_chunked_not_truncated(env):
    from app.services.resume_service import _split_chunks, analyze_resume
    _, fake, *_ = env
    long_text = "\n\n".join(f"Role {i}: Support Executive at Company{i}. Handled Zendesk tickets." for i in range(400))
    chunks = _split_chunks(long_text, 5000)
    assert len(chunks) > 3 and "".join(chunks).replace("\n\n", "").replace("\n", "") != ""
    assert sum(len(c) for c in chunks) >= len(long_text) * 0.99   # nothing lost
    assert analyze_resume(long_text, fake) is not None


def test_access_control_and_gcs_storage(env, monkeypatch):
    client, fake, state, sm = env
    from app.config import settings
    from app.services import storage

    cand = _upload(client).json()
    sid = client.post("/session/start", json={"candidate_id": cand["candidate_id"]}).json()["session_id"]

    # no token / wrong token -> rejected on every candidate endpoint
    for method, path, kw in [
        ("post", f"/session/{sid}/voice-answer", {"files": {"audio": ("a.wav", b"x")}}),
        ("post", f"/session/{sid}/no-response", {}),
        ("post", f"/session/{sid}/events", {"json": {"events": []}}),
        ("post", f"/session/{sid}/end", {}),
        ("get", f"/session/{sid}/result", {}),
    ]:
        assert getattr(client, method)(path, headers={"X-Anonymous": "1"}, **kw).status_code == 403
        bad = getattr(client, method)(path, headers={"X-Anonymous": "1", "X-Session-Token": "nope"}, **kw)
        assert bad.status_code == 403

    # recruiters can read results with the admin key
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "s3cret")
    assert client.get(f"/session/{sid}/result", headers={"X-Anonymous": "1", "X-API-Key": "s3cret"}).status_code == 200
    assert client.get(f"/session/{sid}/result", headers={"X-Anonymous": "1", "X-API-Key": "wrong"}).status_code == 403

    # Google Cloud Storage backend (fake client): file uploaded, local copy removed, gs:// stored
    uploaded = {}

    class FakeBlob:
        def __init__(self, name): self.name = name
        def upload_from_filename(self, path, content_type=None): uploaded[self.name] = (Path(path).read_bytes(), content_type)

    class FakeBucket:
        def blob(self, name): return FakeBlob(name)

    class FakeClient:
        def bucket(self, name): return FakeBucket()

    monkeypatch.setattr(storage, "_client", FakeClient())
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "gcs")
    monkeypatch.setattr(settings, "GCS_BUCKET", "my-bucket")
    up = client.post(f"/session/{sid}/video", files={"video": ("interview.webm", b"v" * 100, "video/webm")}).json()
    assert up["video_path"] == f"gs://my-bucket/video-agent/videos/interview_{sid}.webm"
    assert uploaded[f"video-agent/videos/interview_{sid}.webm"] == (b"v" * 100, "video/webm")
    assert not Path(settings.UPLOAD_DIR, "videos", f"interview_{sid}.webm").exists()
    res = _upload(client, name="Gcs").json()
    assert res["candidate_id"]
    assert any(k.startswith("video-agent/resumes/") for k in uploaded)


def test_consent_form_and_recording(env):
    client, *_ = env
    form = client.get("/config/consent").json()
    assert form["version"] and form["title"] and len(form["sections"]) >= 4 and len(form["statements"]) >= 2
    assert "$" not in str(form)                              # every placeholder was filled
    assert "Priya" in str(form)                              # interviewer name comes from settings

    # a wrong / missing consent version is refused, nothing is stored
    assert _upload(client, consent=False).status_code == 400
    assert client.post("/resume/upload", data={"name": "X"}, files={"file": ("cv.txt", RESUME.encode())}).status_code == 422

    ok = _upload(client)
    assert ok.status_code == 200
    from app.db.database import SessionLocal
    from app.db.models import Candidate
    with SessionLocal() as db:
        cand = db.get(Candidate, ok.json()["candidate_id"])
        assert cand.consent_version == form["version"] and cand.consented_at is not None
