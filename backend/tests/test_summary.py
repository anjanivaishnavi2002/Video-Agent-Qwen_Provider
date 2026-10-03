"""Post-interview summary + observable-event report."""
from types import SimpleNamespace
from datetime import datetime

from tests.test_interview_flow import _upload, _voice, env  # noqa: F401

from app.providers.llm_errors import LLMRateLimitError
from app.services import summary_service


def _event(kind, offset):
    return SimpleNamespace(event_type=kind, offset_ms=offset, details=None, occurred_at=datetime.utcnow())


def test_event_report_is_plain_counting():
    report = summary_service.summarize_events([
        _event("face_missing", 1000), _event("face_returned", 4500),
        _event("head_movement", 5000), _event("face_missing", 9000),
    ])
    assert report["counts"] == {"face_missing": 2, "face_returned": 1, "head_movement": 1}
    assert report["face_missing_seconds"] == 3.5 and report["face_missing_at_end"] is True
    assert "emotion" in report["note"]            # states what the events do NOT mean


class FakeSummaryLLM:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def chat_json(self, messages, schema, **kw):
        self.calls.append(messages)
        if self.fail:
            raise LLMRateLimitError("The AI service is rate-limited right now.")
        return {"overview": "Short factual overview.", "topics_discussed": ["voice support"],
                "stated_experience": ["Handled inbound calls"], "unanswered_or_unclear": [],
                "follow_up_topics": ["CRM tools"]}


def _finished_interview(client, state):
    sid = _upload(client).json()["candidate_id"]
    started = client.post("/session/start", json={"candidate_id": sid}).json()
    session_id = started["session_id"]
    state["texts"] = ["I handled inbound calls.", "Mostly billing disputes."]
    for _ in range(2):
        assert _voice(client, session_id).status_code == 200
    client.post(f"/session/{session_id}/end", json={"reason": "candidate_ended"})
    return session_id


def test_summary_is_generated_after_the_recording_upload(env, monkeypatch):  # noqa: F811
    client, _, state, _ = env
    fake = FakeSummaryLLM()
    monkeypatch.setattr(summary_service, "get_llm", lambda: fake)
    session_id = _finished_interview(client, state)

    client.post(f"/session/{session_id}/events", json={"events": [
        {"type": "face_missing", "offset_ms": 1000}, {"type": "face_returned", "offset_ms": 3000},
        {"type": "multiple_faces", "offset_ms": 5000, "details": {"faces": 2}}]})
    assert client.post(f"/session/{session_id}/video",
                       files={"video": ("rec.webm", b"webm-bytes", "video/webm")}).status_code == 200

    result = client.get(f"/session/{session_id}/result").json()["summary"]   # background task already ran
    assert result["status"] == "ready"
    assert result["interview"]["overview"] == "Short factual overview."
    assert result["recording"]["uploaded"] is True
    assert result["recording"]["events"]["counts"] == {"face_missing": 1, "face_returned": 1, "multiple_faces": 1}
    assert result["recording"]["events"]["face_missing_seconds"] == 2.0
    prompt = fake.calls[0][1]["content"]
    assert "I handled inbound calls." in prompt and "Candidate:" in prompt
    assert "do NOT score" in fake.calls[0][0]["content"]          # no scoring / emotion inference


def test_summary_failure_is_reported_then_can_be_retried(env, monkeypatch):  # noqa: F811
    client, _, state, _ = env
    session_id = _finished_interview(client, state)

    monkeypatch.setattr(summary_service, "get_llm", lambda: FakeSummaryLLM(fail=True))
    failed = client.post(f"/session/{session_id}/summary").json()
    assert failed["status"] == "failed" and "rate-limited" in failed["error"]
    assert "interview" not in failed                              # nothing invented on failure

    monkeypatch.setattr(summary_service, "get_llm", lambda: FakeSummaryLLM())
    assert client.post(f"/session/{session_id}/summary").json()["status"] == "ready"   # failed -> regenerated
    fake2 = FakeSummaryLLM()
    monkeypatch.setattr(summary_service, "get_llm", lambda: fake2)
    assert client.post(f"/session/{session_id}/summary").json()["status"] == "ready"
    assert fake2.calls == []                                      # cached: no second model call
    client.post(f"/session/{session_id}/summary?force=true")
    assert len(fake2.calls) == 1


def test_no_answers_means_no_summary(env, monkeypatch):  # noqa: F811
    client, *_ = env
    monkeypatch.setattr(summary_service, "get_llm", lambda: FakeSummaryLLM())
    started = client.post("/session/start", json={"candidate_id": _upload(client).json()["candidate_id"]}).json()
    client.post(f"/session/{started['session_id']}/end", json={})
    assert client.post(f"/session/{started['session_id']}/summary").json()["status"] == "skipped"
