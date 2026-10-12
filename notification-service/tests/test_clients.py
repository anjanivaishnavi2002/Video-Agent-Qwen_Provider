import httpx
import pytest

from app.clients.resend import ResendClient, ResendError
from app.clients.twilio import TwilioClient, TwilioError


def _http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resend_sends_expected_payload_and_returns_id():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"id": "em_1"})

    c = ResendClient("re_key", "Hi <hr@x.com>", http=_http(handler))
    assert c.send(to="a@b.com", subject="S", text="T", html="<p>T</p>", reply_to="r@x.com") == "em_1"
    assert seen["auth"] == "Bearer re_key" and '"reply_to":"r@x.com"' in seen["body"].replace(" ", "")


@pytest.mark.parametrize("status,retry", [(422, False), (429, True), (500, True)])
def test_resend_errors_say_whether_to_retry(status, retry):
    c = ResendClient("k", "a@x.com", http=_http(lambda r: httpx.Response(status, json={"message": "nope"})))
    with pytest.raises(ResendError) as err:
        c.send(to="a@b.com", subject="S", text="T")
    assert err.value.retryable is retry


def test_resend_requires_credentials_and_body():
    with pytest.raises(ValueError):
        ResendClient("", "a@x.com")
    with pytest.raises(ValueError):
        ResendClient("k", "a@x.com").send(to="a@b.com", subject="S")


def test_twilio_sms_and_call():
    calls = []

    def handler(request):
        calls.append((str(request.url), request.read().decode()))
        return httpx.Response(201, json={"sid": "SM1"})

    t = TwilioClient("AC1", "tok", from_number="+14155550100", http=_http(handler))
    assert t.send_sms(to="+919876543210", body="hello") == "SM1"
    assert t.place_call(to="+919876543210", message="hi & bye") == "SM1"
    assert calls[0][0].endswith("/Accounts/AC1/Messages.json") and "From=%2B14155550100" in calls[0][1]
    assert calls[1][0].endswith("/Calls.json") and "%26amp%3B" in calls[1][1]       # message is XML-escaped


def test_twilio_messaging_service_and_missing_sender():
    t = TwilioClient("AC1", "tok", messaging_service_sid="MG1", http=_http(lambda r: httpx.Response(201, json={})))
    assert t.can_sms and not t.can_call
    with pytest.raises(ValueError):
        t.place_call(to="+1", message="x")
    with pytest.raises(TwilioError) as err:
        TwilioClient("AC1", "tok", from_number="+1", http=_http(lambda r: httpx.Response(503))).send_sms(to="+2", body="x")
    assert err.value.retryable


def test_service_endpoints_and_resend_provider(monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    from app import config
    monkeypatch.setattr(config.settings, "NOTIFICATION_API_TOKEN", "t")
    monkeypatch.setattr(config.settings, "REQUIRE_TOKEN", True)
    monkeypatch.setattr(config.settings, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(config.settings, "RESEND_API_KEY", "re_k")
    monkeypatch.setattr(config.settings, "EMAIL_FROM", "Hi <hr@x.com>")
    monkeypatch.setattr(config.settings, "SMS_PROVIDER", "console")
    monkeypatch.setattr(config.settings, "VOICE_PROVIDER", "console")
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None, **_):
        sent["json"] = json
        return httpx.Response(200, json={"id": "em_9"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    from app import main
    importlib.reload(main)
    h = {"X-Notification-Token": "t"}
    with TestClient(main.app) as c:
        r = c.post("/v1/email", json={"to": "a@b.com", "subject": "S", "text": "T"}, headers=h)
        assert r.json() == {"status": "sent", "provider": "resend", "message_id": "em_9"}
        assert sent["json"]["from"] == "Hi <hr@x.com>"
        assert c.post("/v1/email", json={"to": "a@b.com", "subject": "S"}, headers=h).status_code == 400
        assert c.post("/v1/email", json={"to": "a@b.com", "subject": "S", "text": "T"}).status_code == 401
        assert c.post("/v1/sms", json={"to": "+91 98765 43210", "body": "x"}, headers=h).json()["status"] == "sent"
        assert c.post("/v1/call", json={"to": "+91 98765 43210", "message": "x"}, headers=h).json()["status"] == "sent"
        assert c.post("/v1/sms", json={"to": "abc", "body": "x"}, headers=h).status_code == 400


def test_msg91_flow_payload_and_number_normalising():
    from app.clients.msg91 import Msg91Client, Msg91Error, normalize_mobile
    seen = {}

    def handler(request):
        seen["key"] = request.headers["authkey"]
        seen["body"] = request.read().decode().replace(" ", "")
        return httpx.Response(200, json={"type": "success", "message": "req_123"})

    c = Msg91Client("k", "tpl1", http=_http(handler))
    assert c.send_flow(to="+91 98765 43210", variables={"message": "hi"}) == "req_123"
    assert seen["key"] == "k" and '"mobiles":"919876543210"' in seen["body"] and '"template_id":"tpl1"' in seen["body"]
    assert normalize_mobile("9876543210") == "919876543210"
    with pytest.raises(ValueError):
        normalize_mobile("123")
    err = Msg91Client("k", "t", http=_http(lambda r: httpx.Response(200, json={"type": "error", "message": "bad template"})))
    with pytest.raises(Msg91Error) as e:
        err.send_flow(to="9876543210", variables={"message": "x"})
    assert "bad template" in str(e.value) and e.value.retryable is False
    with pytest.raises(ValueError):
        Msg91Client("", "t")
    with pytest.raises(ValueError):
        Msg91Client("k", "")


def test_msg91_provider_is_selectable():
    from app.config import Settings
    from app.providers.sms import build_sms_provider
    cfg = Settings(SMS_PROVIDER="msg91", MSG91_AUTH_KEY="k", MSG91_TEMPLATE_ID="t")
    assert build_sms_provider(cfg).name == "msg91"


def test_msg91_without_template_starts_but_send_reports_it():
    from app.config import Settings
    from app.providers.base import ProviderError
    from app.providers.sms import build_sms_provider
    provider = build_sms_provider(Settings(SMS_PROVIDER="msg91", MSG91_AUTH_KEY="k", MSG91_TEMPLATE_ID=""))
    with pytest.raises(ProviderError) as e:
        provider.send(to="+919876543210", body="hi")
    assert "template" in str(e.value).lower()
