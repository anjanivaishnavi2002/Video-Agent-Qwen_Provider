"""Admin authentication + authorisation (enforced by the backend), candidates, jobs, evaluation, notifications."""
import time

import httpx
import jwt
import pytest

from tests.test_interview_flow import AuthClient, RESUME, _start, _upload, _voice, env  # noqa: F401

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import AdminUser, Candidate, Interview
from app.security import create_admin_token, hash_password

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="module")
def admin_env(env):  # noqa: F811
    client, fake, state, sm = env
    with SessionLocal() as db:
        for email, role in (("boss@example.com", "admin"), ("rec@example.com", "recruiter")):
            if not db.query(AdminUser).filter_by(email=email).first():
                db.add(AdminUser(email=email, role=role, full_name=role, password_hash=hash_password(PASSWORD)))
        db.commit()
    return client, fake, state


def login(client, email="boss@example.com", password=PASSWORD):
    return client.post("/admin/auth/login", json={"email": email, "password": password},
                       headers={"X-Anonymous": "1"})


def auth(client, email="boss@example.com"):
    return {"Authorization": "Bearer " + login(client, email).json()["access_token"], "X-Anonymous": "1"}


# ---------------------------------------------------------------- authentication
def test_login_success_and_failure(admin_env):
    client, *_ = admin_env
    ok = login(client)
    assert ok.status_code == 200 and ok.json()["admin"]["role"] == "admin" and ok.json()["token_type"] == "bearer"
    assert "password" not in ok.text.lower().replace("password_hash", "")
    assert login(client, password="wrong-password-123").status_code == 401
    assert login(client, email="nobody@example.com").status_code == 401        # same answer: no user enumeration


def test_login_is_throttled(admin_env):
    client, *_ = admin_env
    for _ in range(settings.LOGIN_MAX_ATTEMPTS):
        assert login(client, email="victim@example.com", password="bad-password-1").status_code == 401
    assert login(client, email="victim@example.com", password="bad-password-1").status_code == 429


def test_every_admin_route_requires_a_valid_admin_token(admin_env):
    """Not just the frontend: calling any /admin endpoint without a token must fail on the backend."""
    client, *_ = admin_env
    from app.main import app
    paths = app.openapi()["paths"]
    checked = 0
    for path, methods in paths.items():
        if not path.startswith("/admin") or path == "/admin/auth/login":
            continue
        concrete = path.replace("{candidate_id}", "1").replace("{job_id}", "1").replace("{interview_id}", "1") \
                       .replace("{admin_id}", "1")
        for method in methods:
            r = client.request(method.upper(), concrete, headers={"X-Anonymous": "1"})
            assert r.status_code == 401, f"{method.upper()} {path} answered {r.status_code} without a token"
            bad = client.request(method.upper(), concrete,
                                 headers={"X-Anonymous": "1", "Authorization": "Bearer not.a.token"})
            assert bad.status_code == 401
            checked += 1
    assert checked >= 20


def test_candidate_session_token_cannot_open_admin_api(admin_env):
    client, *_ = admin_env
    cand = _upload(client).json()
    started = _start(client, cand).json()
    r = client.get("/admin/candidates", headers={"X-Anonymous": "1", "X-Session-Token": started["session_token"],
                                                "Authorization": f"Bearer {started['session_token']}"})
    assert r.status_code == 401


def test_expired_wrong_secret_and_wrong_audience_tokens_are_rejected(admin_env):
    client, *_ = admin_env
    with SessionLocal() as db:
        admin = db.query(AdminUser).filter_by(email="boss@example.com").first()
        good = create_admin_token(admin)[0]
        claims = jwt.decode(good, settings.JWT_SECRET, algorithms=["HS256"], audience="video-agent-admin")
    h = lambda t: {"Authorization": f"Bearer {t}", "X-Anonymous": "1"}  # noqa: E731
    assert client.get("/admin/auth/me", headers=h(good)).status_code == 200
    expired = jwt.encode({**claims, "exp": int(time.time()) - 10}, settings.JWT_SECRET, algorithm="HS256")
    forged = jwt.encode(claims, "x" * 40, algorithm="HS256")
    wrong_aud = jwt.encode({**claims, "aud": "someone-else"}, settings.JWT_SECRET, algorithm="HS256")
    none_alg = jwt.encode(claims, None, algorithm="none")
    for token in (expired, forged, wrong_aud, none_alg):
        assert client.get("/admin/auth/me", headers=h(token)).status_code == 401


def test_logout_invalidates_the_token_server_side(admin_env):
    client, *_ = admin_env
    headers = auth(client, "rec@example.com")
    assert client.get("/admin/auth/me", headers=headers).status_code == 200
    assert client.post("/admin/auth/logout", headers=headers).status_code == 200
    assert client.get("/admin/auth/me", headers=headers).status_code == 401


def test_role_based_authorisation(admin_env):
    client, *_ = admin_env
    rec = auth(client, "rec@example.com")
    assert client.get("/admin/candidates", headers=rec).status_code == 200            # recruiters can work
    assert client.get("/admin/users", headers=rec).status_code == 403                 # ...but not manage admins
    assert client.delete("/admin/candidates/99999", headers=rec).status_code == 403   # ...or delete data
    boss = auth(client)
    assert client.get("/admin/users", headers=boss).status_code == 200


def test_inactive_admin_cannot_use_an_old_token(admin_env):
    client, *_ = admin_env
    with SessionLocal() as db:
        db.add(AdminUser(email="gone@example.com", role="recruiter", password_hash=hash_password(PASSWORD)))
        db.commit()
    headers = auth(client, "gone@example.com")
    with SessionLocal() as db:
        db.query(AdminUser).filter_by(email="gone@example.com").first().is_active = False
        db.commit()
    assert client.get("/admin/auth/me", headers=headers).status_code == 401
    assert login(client, "gone@example.com").status_code == 401


# ---------------------------------------------------------------- candidate flow rules
def test_interview_start_needs_the_invite_token_and_respects_attempt_limit(admin_env, monkeypatch):
    client, *_ = admin_env
    cand = _upload(client).json()
    no_token = client.post("/session/start", json={"candidate_id": cand["candidate_id"], "invite_token": "x" * 20})
    assert no_token.status_code == 403
    assert client.post("/session/start", json={"candidate_id": 424242, "invite_token": "x" * 20}).status_code == 403
    monkeypatch.setattr(settings, "MAX_INTERVIEW_ATTEMPTS", 1)
    assert _start(client, cand).status_code == 200
    again = _start(client, cand)
    assert again.status_code == 409 and "attempts" in again.json()["detail"]


def test_resume_upload_validates_input_and_file_signature(admin_env):
    client, *_ = admin_env
    version = client.get("/config/consent").json()["version"]

    def post(**over):
        data = {"name": "Asha", "consent_version": version, "email": "asha@example.com", **over.pop("data", {})}
        files = over.pop("files", {"file": ("cv.txt", RESUME.encode())})
        return client.post("/resume/upload", data=data, files=files)

    assert post(data={"email": "not-an-email"}).status_code == 400
    assert post(data={"phone": "abc"}).status_code == 400
    assert post(data={"experience_years": "99"}).status_code == 400
    assert post(data={"job_id": "9999"}).status_code == 400
    assert post(files={"file": ("cv.pdf", b"MZ\x90 this is an exe renamed")}).status_code == 400
    assert post(files={"file": ("cv.docx", b"not a zip file at all" * 10)}).status_code == 400
    ok = post(data={"phone": "+91 98765 43210", "location": "Hyderabad", "experience_years": "2.5",
                    "skills": "Voice, CRM, voice"})
    assert ok.status_code == 200
    with SessionLocal() as db:
        c = db.get(Candidate, ok.json()["candidate_id"])
        assert c.skills == ["Voice", "CRM"] and c.location == "Hyderabad" and c.experience_years == 2.5
        assert c.interview_status == "applied" and c.invite_token and c.resume_content_type == "text/plain"


# ---------------------------------------------------------------- jobs, candidates, evaluation
def test_job_description_reaches_the_interviewer_and_evaluation_is_stored(admin_env):
    client, fake, state = admin_env
    boss = auth(client)
    job = client.post("/admin/jobs", headers=boss, json={
        "title": "Voice Support Agent", "description": "Handle inbound customer calls for a US telecom client.",
        "location": "Hyderabad", "required_skills": ["English", "CRM"]}).json()
    assert client.get("/jobs").json()[0]["title"] == "Voice Support Agent"
    assert "description" not in client.get("/jobs").json()[0]                  # public list is limited

    cand = _upload(client, data=None) if False else _upload(client, job_id=str(job["id"]), skills="Voice")
    cand = cand.json()
    started = _start(client, cand).json()
    system = fake.calls[-1][0]["content"]
    assert "Voice Support Agent" in system and "inbound customer calls" in system and "English, CRM" in system
    assert "Voice" in system                                                    # self-reported skills
    assert "ravi@example.com" not in system                                     # no contact details in the prompt
    sid = started["session_id"]
    state["texts"] = ["I handled calls.", "Billing mostly.", "Yes.", "Ok.", "Sure."]
    for _ in range(5):
        if _voice(client, sid).json().get("finished"):
            break

    detail = client.get(f"/admin/candidates/{cand['candidate_id']}", headers=boss).json()
    assert detail["interview_status"] == "completed" and detail["interview_attempts"] == 1
    assert detail["interview_score"] == 72 and detail["latest_evaluation"]["recommendation"] == "fit"
    assert detail["interviews"][0]["has_recording"] is False
    interview = client.get(f"/admin/interviews/{sid}", headers=boss).json()
    assert interview["evaluation"]["strengths"] and len(interview["transcript"]) >= 4
    assert client.get("/admin/interviews?status=finished", headers=boss).json()["total"] >= 1

    with SessionLocal() as db:                      # transcript mirrored into interview_turns
        assert len(db.get(Interview, sid).turns) == len(interview["transcript"])

    # candidates never see the evaluation
    assert "evaluation" not in client.get(f"/session/{sid}/result", headers={"X-Session-Token": started["session_token"]}).text


def test_candidate_list_search_filter_and_update(admin_env):
    client, *_ = admin_env
    boss = auth(client)
    _upload(client, name="Zed Unique", email="zed.unique@example.com", location="Pune")
    found = client.get("/admin/candidates", headers=boss, params={"q": "zed.unique"}).json()
    assert found["total"] == 1 and found["items"][0]["name"] == "Zed Unique"
    assert client.get("/admin/candidates", headers=boss, params={"q": "%"}).json()["total"] == 0   # wildcard is escaped
    assert client.get("/admin/candidates", headers=boss, params={"status": "applied", "sort": "name"}).status_code == 200
    cid = found["items"][0]["id"]
    r = client.patch(f"/admin/candidates/{cid}", headers=boss, json={"interview_status": "shortlisted", "phone": "+911234567890"})
    assert r.status_code == 200 and r.json()["interview_status"] == "shortlisted"
    assert client.patch(f"/admin/candidates/{cid}", headers=boss, json={"interview_status": "hired!"}).status_code == 400
    assert client.patch(f"/admin/candidates/{cid}", headers=boss, json={"email": "bad"}).status_code == 400
    assert "resume_text" not in client.get(f"/admin/candidates/{cid}", headers=boss).json()
    assert client.get(f"/admin/candidates/{cid}?include_resume_text=true", headers=boss).json()["resume_text"]


def test_resume_access_local_and_signed_url(admin_env, monkeypatch):
    client, *_ = admin_env
    boss = auth(client)
    cid = _upload(client, name="Resume Owner", email="ro@example.com").json()["candidate_id"]
    r = client.get(f"/admin/candidates/{cid}/resume", headers=boss)
    assert r.status_code == 200 and b"Customer Support Executive" in r.content          # local dev: the file itself
    from app.services import storage
    with SessionLocal() as db:
        db.get(Candidate, cid).resume_path = "gs://private-bucket/video-agent/resumes/x.pdf"
        db.commit()
    monkeypatch.setattr(storage, "signed_url", lambda uri, **kw: f"https://signed.example/{uri[5:]}?sig=1")
    out = client.get(f"/admin/candidates/{cid}/resume", headers=boss).json()
    assert out["url"].startswith("https://signed.example/private-bucket/") and out["expires_in_seconds"] == settings.SIGNED_URL_TTL_SECONDS
    assert client.get(f"/admin/candidates/{cid}/resume").status_code in (401, 403)


def test_delete_candidate_removes_everything(admin_env):
    client, *_ = admin_env
    boss = auth(client)
    cid = _upload(client, name="Delete Me", email="dm@example.com").json()["candidate_id"]
    assert client.delete(f"/admin/candidates/{cid}", headers=boss).json() == {"status": "deleted"}
    assert client.get(f"/admin/candidates/{cid}", headers=boss).status_code == 404


# ---------------------------------------------------------------- notifications
def test_invitation_goes_through_the_notification_service(admin_env, monkeypatch):
    client, *_ = admin_env
    boss = auth(client)
    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append((url, json, headers))
        return httpx.Response(200, json={"status": "sent", "provider": "console", "message_id": "m1"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(settings, "NOTIFICATION_SERVICE_URL", "https://notify.example")
    monkeypatch.setattr(settings, "NOTIFICATION_SERVICE_TOKEN", "svc-token")
    monkeypatch.setattr(httpx, "post", fake_post)
    cid = _upload(client, name="Invitee", email="inv@example.com", phone="+91 98765 43210").json()["candidate_id"]
    out = client.post(f"/admin/candidates/{cid}/invite", headers=boss, json={}).json()
    assert {n["channel"] for n in out["notifications"]} == {"email", "sms"}
    assert all(n["status"] == "sent" for n in out["notifications"])
    url, body, headers = sent[0]
    assert url == "https://notify.example/v1/send" and headers["X-Notification-Token"] == "svc-token"
    assert "?invite=" in body["context"]["interview_link"]
    assert out["candidate"]["interview_status"] == "invited"
    assert client.get("/admin/notifications", headers=boss, params={"candidate_id": cid}).json()["total"] == 2

    # a provider outage is recorded, never raised into the admin flow
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    again = client.post(f"/admin/candidates/{cid}/notify", headers=boss, json={"kind": "reminder", "channels": ["email"]})
    assert again.status_code == 200 and again.json()["notifications"][0]["status"] == "failed"


def test_notifications_are_skipped_without_contact_details_or_service(admin_env, monkeypatch):
    client, *_ = admin_env
    boss = auth(client)
    monkeypatch.setattr(settings, "NOTIFICATION_SERVICE_URL", "")
    cid = _upload(client, name="Quiet", email="quiet@example.com").json()["candidate_id"]
    out = client.post(f"/admin/candidates/{cid}/notify", headers=boss, json={"kind": "reminder"}).json()
    assert out["notifications"][0]["status"] == "skipped"
    assert client.post(f"/admin/candidates/{cid}/notify", headers=boss, json={"kind": "spam"}).status_code == 400


# ---------------------------------------------------------------- platform
def test_health_is_dependency_free_and_ready_checks_database(admin_env, monkeypatch):
    client, *_ = admin_env
    assert client.get("/ready").status_code == 503                     # no GOOGLE_CLOUD_PROJECT configured
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "proj")
    assert client.get("/health").json() == {"status": "healthy"}
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["database"] is True


def test_direct_video_upload_flow_for_cloud_run(admin_env, monkeypatch):
    client, *_ = admin_env
    from app.services import storage
    cand = _upload(client).json()
    sid = _start(client, cand).json()["session_id"]
    assert client.post(f"/session/{sid}/video/upload-url", json={}).json() == {"mode": "direct"}

    monkeypatch.setattr(settings, "STORAGE_BACKEND", "gcs")
    monkeypatch.setattr(settings, "GCS_BUCKET", "b")
    monkeypatch.setattr(storage, "signed_url", lambda uri, **kw: f"https://signed/{uri[5:]}")
    monkeypatch.setattr(storage, "object_size", lambda uri: 4096)
    out = client.post(f"/session/{sid}/video/upload-url", json={"content_type": "video/webm;codecs=vp8"}).json()
    assert out["mode"] == "gcs" and out["url"] == f"https://signed/b/video-agent/videos/interview_{sid}.webm"
    assert out["headers"]["x-goog-content-length-range"].startswith("1,")
    assert client.post(f"/session/{sid}/video/upload-url", json={"content_type": "text/html"}).status_code == 400
    done = client.post(f"/session/{sid}/video/complete", json={"content_type": "video/webm"})
    assert done.status_code == 200 and done.json()["size_bytes"] == 4096
    with SessionLocal() as db:
        assert db.get(Interview, sid).video_path == f"gs://b/video-agent/videos/interview_{sid}.webm"


def test_production_configuration_guards():
    from pydantic import ValidationError
    from app.config import Settings
    base = dict(DATABASE_URL="sqlite://", ENVIRONMENT="production")
    with pytest.raises(ValidationError):
        Settings(**base)                                         # no JWT secret, local storage ...
    good = dict(base, AUTO_CREATE_SCHEMA=False, ENABLE_DEBUG_ENDPOINTS=False, JWT_SECRET="x" * 40, STORAGE_BACKEND="gcs", GCS_BUCKET_NAME="b",
                CORS_ORIGINS="https://app.example.com")
    assert Settings(**good).is_production
    for bad in ({"CORS_ORIGINS": "*"}, {"ENABLE_DEBUG_ENDPOINTS": True}, {"AUTO_CREATE_SCHEMA": True},
                {"JWT_SECRET": "short"}):
        with pytest.raises(ValidationError):
            Settings(**{**good, **bad})


def test_admin_can_call_and_send_a_custom_message(admin_env, monkeypatch):
    client, *_ = admin_env
    boss = auth(client)
    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return httpx.Response(200, json={"status": "sent", "provider": "console", "message_id": "m1"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(settings, "NOTIFICATION_SERVICE_URL", "https://notify.example")
    monkeypatch.setattr(httpx, "post", fake_post)
    cid = _upload(client, name="Callee", email="c@example.com", phone="+91 98765 43210").json()["candidate_id"]
    url = f"/admin/candidates/{cid}/notify"
    # a custom message needs text
    assert client.post(url, headers=boss, json={"kind": "custom", "channels": ["call"]}).status_code == 400
    ok = client.post(url, headers=boss, json={"kind": "custom", "channels": ["email", "sms", "call"],
                                              "message": "Please call us back.", "subject": "Hello"})
    assert ok.status_code == 200 and [n["channel"] for n in ok.json()["notifications"]] == ["email", "sms", "call"]
    assert {b["channel"] for b in sent} == {"email", "sms", "call"}
    assert sent[0]["context"]["message"] == "Please call us back."
    assert client.post(url, headers=boss, json={"kind": "reminder", "channels": ["fax"]}).status_code == 400
    # not available without an admin token
    assert client.post(url, json={"kind": "custom", "channels": ["call"], "message": "x"}).status_code == 401
