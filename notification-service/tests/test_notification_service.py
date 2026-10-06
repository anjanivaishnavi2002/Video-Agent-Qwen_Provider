import importlib

import pytest
from fastapi.testclient import TestClient

from app import config


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(config.settings, "NOTIFICATION_API_TOKEN", "secret-token")
    monkeypatch.setattr(config.settings, "REQUIRE_TOKEN", True)
    monkeypatch.setattr(config.settings, "EMAIL_PROVIDER", "console")
    monkeypatch.setattr(config.settings, "SMS_PROVIDER", "console")
    from app import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


H = {"X-Notification-Token": "secret-token"}
BODY = {"channel": "email", "kind": "invitation", "to": "a@b.com",
        "context": {"candidate_name": "Asha", "job_title": "Agent", "interview_link": "https://x/?invite=t"}}


def test_health_needs_nothing(client):
    assert client.get("/health").json() == {"status": "healthy"}


def test_send_requires_token(client):
    assert client.post("/v1/send", json=BODY).status_code == 401
    assert client.post("/v1/send", json=BODY, headers={"X-Notification-Token": "nope"}).status_code == 401


def test_email_and_sms_sent(client):
    r = client.post("/v1/send", json=BODY, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "sent"
    sms = {**BODY, "channel": "sms", "to": "+91 98765 43210"}
    assert client.post("/v1/send", json=sms, headers=H).json()["status"] == "sent"


def test_validation(client):
    assert client.post("/v1/send", json={**BODY, "to": "not-an-email"}, headers=H).status_code == 400
    assert client.post("/v1/send", json={**BODY, "kind": "spam"}, headers=H).status_code == 400
    assert client.post("/v1/send", json={**BODY, "channel": "fax"}, headers=H).status_code == 422


def test_templates_escape_html():
    from app.templates import render_email
    _, _, html = render_email("reminder", {"candidate_name": "<script>x</script>", "interview_link": "https://x"})
    assert "<script>" not in html


def test_provider_factory_rejects_unknown():
    from app.providers.email import build_email_provider
    s = config.Settings(EMAIL_PROVIDER="carrier-pigeon")
    with pytest.raises(ValueError):
        build_email_provider(s)
    with pytest.raises(ValueError):
        build_email_provider(config.Settings(EMAIL_PROVIDER="smtp"))   # missing host


def test_call_and_custom_message(client):
    call = {"channel": "call", "kind": "custom", "to": "+91 98765 43210",
            "context": {"candidate_name": "Asha", "message": "Please check your e-mail."}}
    r = client.post("/v1/send", json=call, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "sent"
    # a custom message without text is rejected, not a 500
    bad = {**call, "context": {"candidate_name": "Asha"}}
    assert client.post("/v1/send", json=bad, headers=H).status_code == 400
    # invalid number
    assert client.post("/v1/send", json={**call, "to": "abc"}, headers=H).status_code == 400


def test_voice_script_has_no_link():
    from app.templates import render_voice
    text = render_voice("invitation", {"candidate_name": "Asha", "interview_link": "https://x/?invite=t"})
    assert "http" not in text and "Asha" in text
