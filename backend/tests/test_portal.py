"""Candidate accounts, job board + JD upload, applications, and the paid unlock of interview recordings."""
import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.database import SessionLocal, init_db
from app.db.models import AdminUser, Candidate, Interview, CreditLedger
from app.main import app
from app.security import hash_password
from app.services.consent_service import get_consent

PW = "Passw0rd-long"
RESUME = (b"%PDF-1.4\n" + b"Experienced customer support executive with five years of BPO voice and chat experience. " * 8)


@pytest.fixture()
def client(monkeypatch):
    init_db()
    from app.api import portal
    monkeypatch.setattr(portal, "process_resume_file", lambda f: {
        "resume_filename": "cv.pdf", "resume_path": "/tmp/cv.pdf", "resume_content_type": "application/pdf",
        "resume_size_bytes": 10, "resume_text": "Five years of BPO customer support experience " * 5})
    with SessionLocal() as db:
        for email, role in (("boss2@example.com", "admin"), ("rec2@example.com", "recruiter")):
            if not db.query(AdminUser).filter_by(email=email).first():
                db.add(AdminUser(email=email, role=role, full_name=role, password_hash=hash_password(PW)))
        db.commit()
    return TestClient(app)


def admin_h(client, email="boss2@example.com"):
    r = client.post("/admin/auth/login", json={"email": email, "password": PW})
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def cand_h(client, email):
    r = client.post("/portal/register", json={"email": email, "password": PW, "full_name": "Asha Rao"})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def make_job(client, title="Voice Process Agent"):
    r = client.post("/admin/jobs", headers=admin_h(client), json={
        "title": title, "description": "Handle inbound customer calls for a telecom client.",
        "required_skills": ["English", "CRM"], "status": "open"})
    assert r.status_code == 201, r.text
    return r.json()


def test_register_login_and_wrong_password(client):
    cand_h(client, "asha1@example.com")
    assert client.post("/portal/register", json={"email": "asha1@example.com", "password": PW,
                                                  "full_name": "Asha Rao"}).status_code == 409
    assert client.post("/portal/register", json={"email": "x@example.com", "password": "short",
                                                  "full_name": "Asha Rao"}).status_code == 422
    assert client.post("/portal/register", json={"email": "y@example.com", "password": "onlyletters",
                                                  "full_name": "Asha Rao"}).status_code == 400
    assert client.post("/portal/login", json={"email": "asha1@example.com", "password": "nope-nope1"}).status_code == 401
    ok = client.post("/portal/login", json={"email": "ASHA1@example.com", "password": PW})
    assert ok.status_code == 200 and "password" not in ok.text


def test_portal_needs_a_candidate_token_and_tokens_do_not_cross(client):
    assert client.get("/portal/jobs").status_code == 401
    assert client.get("/portal/jobs", headers=admin_h(client)).status_code == 401      # admin token is not a candidate token
    assert client.get("/admin/jobs", headers=cand_h(client, "asha2@example.com")).status_code == 401


def test_logout_invalidates_the_token(client):
    h = cand_h(client, "asha3@example.com")
    assert client.get("/portal/me", headers=h).status_code == 200
    client.post("/portal/logout", headers=h)
    assert client.get("/portal/me", headers=h).status_code == 401


def test_apply_to_job_and_own_applications_only(client):
    job = make_job(client)
    h = cand_h(client, "asha4@example.com")
    jobs = client.get("/portal/jobs", headers=h).json()
    assert any(j["id"] == job["id"] and not j["applied"] and j["required_skills"] == ["English", "CRM"] for j in jobs)
    data = {"consent_version": get_consent()["version"], "experience_years": "3", "skills": "English, CRM"}
    r = client.post(f"/portal/jobs/{job['id']}/apply", headers=h, data=data,
                    files={"file": ("cv.pdf", io.BytesIO(RESUME), "application/pdf")})
    assert r.status_code == 201, r.text
    app1 = r.json()
    assert app1["job"]["id"] == job["id"] and app1["invite_token"] and app1["can_start"]
    again = client.post(f"/portal/jobs/{job['id']}/apply", headers=h, data=data)
    assert again.json()["candidate_id"] == app1["candidate_id"]                       # no duplicate application
    assert [a["candidate_id"] for a in client.get("/portal/applications", headers=h).json()] == [app1["candidate_id"]]
    other = cand_h(client, "other4@example.com")
    assert client.get("/portal/applications", headers=other).json() == []             # someone else sees nothing
    bad = client.post(f"/portal/jobs/{job['id']}/apply", headers=h, data={**data, "consent_version": "old"})
    assert bad.status_code == 400


def test_second_application_reuses_the_resume_and_closed_jobs_are_refused(client):
    j1, j2 = make_job(client, "Chat Support"), make_job(client, "Email Support")
    h = cand_h(client, "asha5@example.com")
    data = {"consent_version": get_consent()["version"]}
    first = client.post(f"/portal/jobs/{j1['id']}/apply", headers=h, data=data,
                        files={"file": ("cv.pdf", io.BytesIO(RESUME), "application/pdf")})
    assert first.status_code == 201
    assert client.post(f"/portal/jobs/{j2['id']}/apply", headers=h, data=data).status_code == 201   # no new upload
    client.patch(f"/admin/jobs/{j2['id']}", headers=admin_h(client), json={"status": "closed"})
    h2 = cand_h(client, "asha6@example.com")
    assert client.post(f"/portal/jobs/{j2['id']}/apply", headers=h2, data=data).status_code == 400
    assert client.post(f"/portal/jobs/{j1['id']}/apply", headers=h2, data=data).status_code == 400   # no resume yet


def test_admin_jd_upload_extract_and_download(client):
    h = admin_h(client)
    job = make_job(client)
    text = b"Job Description: Voice process agent. Handle inbound calls, English fluency required. " * 3
    ex = client.post("/admin/jobs/extract-jd", headers=h, files={"file": ("jd.txt", io.BytesIO(text), "text/plain")})
    assert ex.status_code == 200 and "Voice process agent" in ex.json()["text"]
    up = client.post(f"/admin/jobs/{job['id']}/jd?fill_description=true", headers=h,
                     files={"file": ("jd.txt", io.BytesIO(text), "text/plain")})
    assert up.status_code == 200 and up.json()["has_jd_file"] and "Voice process agent" in up.json()["description"]
    assert client.get(f"/admin/jobs/{job['id']}/jd", headers=h).status_code == 200
    assert client.post(f"/admin/jobs/{job['id']}/jd", files={"file": ("jd.txt", io.BytesIO(text))}).status_code == 401
    exe = client.post("/admin/jobs/extract-jd", headers=h, files={"file": ("jd.exe", io.BytesIO(b"MZ" * 40))})
    assert exe.status_code == 400


def _finished_interview(job_id):
    with SessionLocal() as db:
        c = Candidate(name="Ravi K", email="ravi@example.com", job_id=job_id, interview_status="completed")
        db.add(c)
        db.commit()
        i = Interview(candidate_id=c.id, job_id=job_id, status="finished", transcript=[{"role": "assistant", "text": "Hi"}],
                      started_at=datetime(2026, 1, 1, 10, 0), ended_at=datetime(2026, 1, 1, 10, 9),
                      video_path="/tmp/none.webm")
        db.add(i)
        db.commit()
        return i.id


def test_interview_is_a_teaser_until_unlocked_with_credits(client, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_UNLOCK", True)
    monkeypatch.setattr(settings, "UNLOCK_CREDIT_COST", 10)
    boss, rec = admin_h(client), admin_h(client, "rec2@example.com")
    job = make_job(client)
    iid = _finished_interview(job["id"])

    teaser = client.get(f"/admin/interviews/{iid}", headers=rec).json()
    assert teaser["locked"] is True and teaser["transcript"] == [] and teaser["job"]["id"] == job["id"]
    assert "email" not in (teaser["candidate"] or {})
    assert client.get(f"/admin/interviews/{iid}/recording", headers=rec).status_code == 402

    before = client.get("/admin/credits", headers=rec).json()["balance"]
    assert client.post(f"/admin/interviews/{iid}/unlock", headers=rec).status_code == 402       # no credits yet
    assert client.post("/admin/credits/grant", headers=rec, json={"amount": 50}).status_code == 403   # recruiters can't mint
    assert client.post("/admin/credits/grant", headers=boss, json={"amount": 25, "note": "test"}).json()["balance"] == before + 25

    first = client.post(f"/admin/interviews/{iid}/unlock", headers=rec).json()
    second = client.post(f"/admin/interviews/{iid}/unlock", headers=rec).json()
    assert first["charged"] == 10 and second["charged"] == 0                                    # idempotent
    assert second["balance"] == before + 15
    full = client.get(f"/admin/interviews/{iid}", headers=rec).json()
    assert full["locked"] is False and full["transcript"] and full["candidate"]["email"] == "ravi@example.com"
    assert client.get(f"/admin/interviews/{iid}/recording", headers=rec).status_code in (200, 404)  # no longer 402
    with SessionLocal() as db:
        assert db.query(CreditLedger).filter_by(unlock_key=f"unlock:{iid}").count() == 1


def test_unlock_endpoints_need_admin_login(client):
    assert client.post("/admin/interviews/1/unlock").status_code == 401
    assert client.get("/admin/credits").status_code == 401
    assert client.post("/admin/credits/grant", json={"amount": 5}).status_code == 401


def _finish_interview(candidate_id, video="recordings/x.webm", **extra):
    from app.services import candidate_service
    with SessionLocal() as db:
        c = db.get(Candidate, candidate_id)
        i = Interview(candidate_id=c.id, status="finished", mode="voice", started_at=datetime.utcnow(),
                      ended_at=datetime.utcnow(), video_path=video, transcript=[{"role": "assistant", "text": "hi"}],
                      **extra)
        db.add(i)
        db.flush()
        candidate_service.mark_started(db, c, i)
        candidate_service.mark_finished(db, c, i)
        db.commit()
        return i.id


def test_interview_attaches_to_jobs_and_the_candidate_chooses_per_job(client, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_UNLOCK", True)
    j1, j2 = make_job(client, "Voice Process"), make_job(client, "Chat Process")
    h = cand_h(client, "once@example.com")
    data = {"consent_version": get_consent()["version"]}
    a1 = client.post(f"/portal/jobs/{j1['id']}/apply", headers=h, data=data,
                     files={"file": ("cv.pdf", io.BytesIO(RESUME), "application/pdf")}).json()
    a2 = client.post(f"/portal/jobs/{j2['id']}/apply", headers=h, data=data).json()
    assert a1["can_start"] and not a1["interview_attached"] and not a2["interview_attached"]
    first = _finish_interview(a1["candidate_id"])                    # finishing attaches to every job without one
    apps = {a["job"]["id"]: a for a in client.get("/portal/applications", headers=h).json()}
    assert apps[j1["id"]]["interview_id"] == first and apps[j2["id"]]["interview_id"] == first
    assert apps[j2["id"]]["status"] == "completed"
    # a second interview can be recorded (up to the limit) and chosen for one job only
    second = _finish_interview(a1["candidate_id"], video="recordings/y.webm")
    listing = client.get("/portal/interviews", headers=h).json()
    assert [i["id"] for i in listing["interviews"]] == [second, first] and listing["can_record_new"]
    r = client.put(f"/portal/applications/{a2['candidate_id']}/interview", headers=h, json={"interview_id": second})
    assert r.status_code == 200 and r.json()["interview_id"] == second
    apps = {a["job"]["id"]: a for a in client.get("/portal/applications", headers=h).json()}
    assert apps[j1["id"]]["interview_id"] == first and apps[j2["id"]]["interview_id"] == second
    other = cand_h(client, "someoneelse@example.com")                # nobody else can attach or see my interviews
    assert client.put(f"/portal/applications/{a2['candidate_id']}/interview", headers=other,
                      json={"interview_id": second}).status_code == 404
    assert client.get("/portal/interviews", headers=other).json()["interviews"] == []
    # admin: each job lists the interview chosen for it; unlocking one charges once
    boss = admin_h(client)
    assert [r["id"] for r in client.get(f"/admin/interviews?job_id={j1['id']}", headers=boss).json()["items"]] == [first]
    assert [r["id"] for r in client.get(f"/admin/interviews?job_id={j2['id']}", headers=boss).json()["items"]] == [second]
    client.post("/admin/credits/grant", headers=boss, json={"amount": 30})
    assert client.post(f"/admin/interviews/{second}/unlock", headers=boss).json()["charged"] == 10
    assert client.post(f"/admin/interviews/{second}/unlock", headers=boss).json()["charged"] == 0
    detail = client.get(f"/admin/candidates/{a2['candidate_id']}", headers=boss).json()
    assert second in [x["id"] for x in detail["interviews"]]


def test_interview_limit_and_deleting_a_recording(client, monkeypatch):
    from app.services import storage
    deleted = []
    monkeypatch.setattr(storage, "delete_file", lambda uri: deleted.append(uri))
    monkeypatch.setattr(settings, "MAX_INTERVIEW_ATTEMPTS", 2)
    j1, j2 = make_job(client, "Voice A"), make_job(client, "Voice B")
    h = cand_h(client, "redo@example.com")
    data = {"consent_version": get_consent()["version"]}
    a1 = client.post(f"/portal/jobs/{j1['id']}/apply", headers=h, data=data,
                     files={"file": ("cv.pdf", io.BytesIO(RESUME), "application/pdf")}).json()
    client.post(f"/portal/jobs/{j2['id']}/apply", headers=h, data=data)
    first = _finish_interview(a1["candidate_id"], video="gs://b/one.webm")
    second = _finish_interview(a1["candidate_id"], video="gs://b/two.webm")
    listing = client.get("/portal/interviews", headers=h).json()
    assert not listing["can_record_new"] and "Delete one" in listing["message"]       # at the limit
    r = client.delete(f"/portal/interviews/{second}", headers=h)
    assert r.status_code == 200 and deleted == ["gs://b/two.webm"] and r.json()["can_record_new"]
    apps = {a["job"]["id"]: a for a in r.json()["applications"]}
    assert apps[j1["id"]]["interview_id"] == first and apps[j2["id"]]["interview_id"] == first   # fell back
    with SessionLocal() as db:
        row = db.get(Interview, second)
        assert row.status == "replaced" and row.video_path is None and row.transcript == []
    assert second not in [x["id"] for x in client.get("/admin/interviews", headers=admin_h(client)).json()["items"]]
    assert client.delete(f"/portal/interviews/{second}", headers=h).status_code == 404


def test_admin_can_add_50_sample_jobs_by_experience_and_candidates_see_them(client):
    boss = admin_h(client)
    first = client.post("/admin/jobs/sample", headers=boss).json()
    again = client.post("/admin/jobs/sample", headers=boss).json()
    assert first["existing"] + first["added"] == 50 and again["added"] == 0
    items = client.get("/admin/jobs?page_size=100", headers=boss).json()["items"]
    sample = [j for j in items if j["experience_min"] is not None]
    assert len(sample) >= 50 and {j["process_type"] for j in sample} >= {"voice", "chat", "email", "blended"}
    assert min(j["experience_min"] for j in sample) == 0 and max(j["experience_max"] for j in sample) >= 10
    assert client.post("/admin/jobs/sample").status_code in (401, 403)
    h = cand_h(client, "samples@example.com")
    cards = client.get("/portal/jobs", headers=h).json()
    assert any(c["title"] == "Chat Support Specialist" and c["experience_min"] == 1 for c in cards)
    bad = client.post("/admin/jobs", headers=boss, json={"title": "Bad band", "description": "A valid description here.",
                                                         "experience_min": 5, "experience_max": 2})
    assert bad.status_code == 400


def test_interviews_stuck_in_running_do_not_block_the_candidate(client):
    from datetime import timedelta
    j = make_job(client, "Stuck Test Job")
    h = cand_h(client, "stuck@example.com")
    app1 = client.post(f"/portal/jobs/{j['id']}/apply", headers=h, data={"consent_version": get_consent()["version"]},
                       files={"file": ("cv.pdf", io.BytesIO(RESUME), "application/pdf")}).json()
    old = datetime.utcnow() - timedelta(minutes=30)
    with SessionLocal() as db:
        c = db.get(Candidate, app1["candidate_id"])
        c.interview_attempts, c.interview_status = 3, "in_progress"           # 3 failed connections, limit reached
        for _ in range(3):
            db.add(Interview(candidate_id=c.id, status="running", started_at=old, updated_at=old, transcript=[]))
        db.commit()
    row = client.get("/portal/applications", headers=h).json()[0]
    assert row["can_start"] and row["status"] == "applied"
    with SessionLocal() as db:
        assert db.get(Candidate, app1["candidate_id"]).interview_attempts == 0
        assert db.query(Interview).filter_by(candidate_id=app1["candidate_id"], status="failed").count() == 3


def test_interviewer_focus_comes_from_the_applied_roles_and_experience(client):
    from app.services import candidate_service
    from app.services.live_service import EXERCISE_RULES, TAB_RULES
    chat = client.post("/admin/jobs", headers=admin_h(client), json={
        "title": "Chat Desk", "description": "Handle chats for a retail client.", "process_type": "chat",
        "experience_min": 1, "experience_max": 3}).json()
    mail = client.post("/admin/jobs", headers=admin_h(client), json={
        "title": "Mail Desk", "description": "Reply to customer emails.", "process_type": "email"}).json()
    h = cand_h(client, "focus@example.com")
    data = {"consent_version": get_consent()["version"], "experience_years": "4"}
    first = client.post(f"/portal/jobs/{chat['id']}/apply", headers=h, data=data,
                        files={"file": ("cv.pdf", io.BytesIO(RESUME), "application/pdf")}).json()
    client.post(f"/portal/jobs/{mail['id']}/apply", headers=h, data=data)
    with SessionLocal() as db:
        c = db.get(Candidate, first["candidate_id"])
        focus = candidate_service.interview_focus(db, c)
        ctx = candidate_service.job_context(db, c)
    assert focus["kinds"] == ["chat", "email"] and focus["level"] == "mid-level"
    assert "Chat Desk" in ctx["title"] and "Mail Desk" in ctx["title"] and "mid-level" in ctx["description"]
    assert "more than twice" in TAB_RULES and "MUST" in EXERCISE_RULES and "Please handle it" in EXERCISE_RULES
