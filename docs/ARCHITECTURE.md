# Architecture - AI Video Interview Platform (Google Cloud)

## 1. Production architecture (text diagram)

```
                       Candidate browser                          Recruiter / admin browser
                  (camera, mic, React app)                       (/admin/login -> admin dashboard)
                         |        |                                       |
        static files     |        | API (JSON, multipart audio)           | API (Bearer JWT)
                         v        v                                       v
                +-------------------+            +------------------------------------------+
                |  interview-       |  API_URL   |  interview-backend  (Cloud Run, FastAPI)  |
                |  frontend         |----------->|  routes: candidates/resume, jobs, session |
                |  (Cloud Run,      |            |  (interview turns), evaluation, admin/*,  |
                |  nginx, static)   |            |  auth                                     |
                +-------------------+            |  in-container: Whisper STT + Piper TTS    |
                                                 +--+---------+----------+----------+-------+
                                                    |         |          |          |
                       Application Default          |         |          |          | HTTPS + ID token
                       Credentials (service acct)   |         |          |          |  + shared token
                                                    v         v          v          v
                                             +----------+ +--------+ +----------+ +---------------------+
                                             | Vertex AI| |Cloud   | |Cloud     | | notification-service |
                                             | Gemini   | |SQL     | |Storage   | | (Cloud Run, private) |
                                             | (API     | |Postgres| |(private  | |  /v1/send            |
                                             |  call)   | |        | | bucket)  | |  e-mail: SMTP/SendGrid|
                                             +----------+ +--------+ +----------+ |  SMS: Twilio          |
                                                  ^           ^           ^       +---------------------+
   Secret Manager -> env vars (DATABASE_URL, JWT_SECRET, notification token, provider keys) injected at deploy
   Artifact Registry -> the three images        Cloud Logging/Monitoring <- structured JSON logs, /health uptime check
```

Three deployable services, nothing else:

| Service | What it is | Exposure | Identity (service account) |
|---|---|---|---|
| `interview-frontend` | static React app behind nginx. No secrets. | public | `interview-frontend` (logging only) |
| `interview-backend` | FastAPI: candidates, jobs, resumes, interviews/sessions, evaluation, admin, auth, notification client | public (it does its own auth) | `interview-backend`: Vertex AI User, Cloud SQL Client, Storage object admin (one bucket), Secret accessor, token creator (self, for signed URLs), invoker of the notification service |
| `notification-service` | e-mail + SMS sender | private (IAM invoker + shared token) | `interview-notify`: Secret accessor only |

Gemini is **not deployed**: the backend calls the Vertex AI API (`google-genai` SDK, `vertexai=True`), authenticated by the Cloud Run service account.

## 2. What was reused and what changed

Reused as-is or extended in place: the FastAPI routers (`/resume`, `/session`, `/config`), the interview engine (`interview_service.py`, `TURN_SCHEMA`, `InterviewSettings`, wrap-up/empty-streak logic), the in-memory session manager with database rehydration, the resume pipeline (extraction, grounded analysis, chunking), `storage.py` (local/GCS), the prompt files, the consent flow, events/recording/summary, the provider error types, the React candidate flow and its audio/face hooks.

Changed or added:

* `providers/gemini_provider.py` (Vertex AI Gemini, ADC only) + `providers/factory.py` (`get_llm()`); the Qwen-on-Vertex provider was removed; Ollama stays as an offline development option.
* `config.py`: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `VERTEX_AI_MODEL`, `GCS_BUCKET_NAME`, JWT/admin, notification, production safety checks (refuses to start in production with a weak JWT secret, `*` CORS, local storage, debug endpoints or `create_all`).
* `db/models`: `AdminUser`, `Job`, `InterviewTurn`, `Evaluation`, `NotificationRecord`; new columns on `Candidate` and `Interview`. Alembic replaces the start-up `ALTER TABLE` code.
* `security.py`: admin JWT + roles + login throttling; `/session/start` now needs the candidate's invite token.
* `api/admin/*` (auth, dashboard, candidates, jobs, interviews, notifications, users, system), `api/candidates.py` (public jobs + invitation lookup).
* `services/`: `evaluation_service`, `notification_client`, `candidate_service`, `admin_service`; storage signed URLs; direct-to-bucket recording upload.
* `notification-service/` (new service), frontend `/admin/*` screens, Cloud Run Dockerfiles, `infra/cloudrun/*`.

## 3. Backend modules (`backend/app`)

| Concern | Where |
|---|---|
| authentication | `security.py`, `api/admin/auth.py` |
| admin | `api/admin/` (`candidates`, `jobs`, `interviews`, `misc` = dashboard, notifications, users, system) |
| candidates / resumes | `api/resume.py` (apply + upload), `api/candidates.py` (jobs, invitation), `services/candidate_service.py` |
| jobs | `api/admin/jobs.py`, public `GET /jobs` |
| interviews / sessions | `api/session.py`, `services/session_manager.py`, `services/interview_service.py` |
| evaluation | `services/evaluation_service.py`, `services/summary_service.py` |
| notifications | `services/notification_client.py` (backend side), `notification-service/` (sender) |
| AI provider | `providers/` (isolated; the rest of the app only calls `get_llm().chat_json(...)`) |

## 4. Database

```
admin_users 1---* jobs (created_by)
jobs        1---* candidates (applied job)        jobs 1---* interviews
candidates  1---* interviews 1---* interview_events
                 interviews 1---* interview_turns (queryable copy of the transcript)
                 interviews 1---1 evaluations ---> candidates
candidates  1---* notification_records (also -> interviews, -> admin_users.triggered_by)
```

Files are **never** stored in PostgreSQL: `candidates.resume_path` and `interviews.video_path` hold `gs://bucket/path` references plus size/content-type metadata. Indexes: FK columns, `candidates.email`, `candidates.interview_status`, `candidates.invite_token` (unique), `admin_users.email` (unique), `interviews.status`, `jobs.status`, `notification_records(candidate_id, created_at)`.

Migrations: `backend/alembic/versions/0001_baseline.py` (the schema before Alembic; creates tables on a new database, only adds missing columns on an existing one) and `0002_platform_schema.py` (additive; back-fills attempts/status/invite tokens/turns for existing rows; no destructive step, no automatic downgrade). Tested on PostgreSQL against both a new and a legacy database with data.

## 5. Admin login flow (`/admin/login`)

1. The browser opens `/admin/login` (any `/admin/*` path without a session shows it).
2. `POST /admin/auth/login {email, password}` -> bcrypt check (constant-time for unknown users), throttled per e-mail+IP, generic error message.
3. The backend returns a 60-minute JWT (`HS256`, `iss`/`aud`, `sub`, `role`, `tv`). The SPA keeps it in `sessionStorage` and sends `Authorization: Bearer ...`.
4. **Every** `/admin/*` route depends on `require_admin` (or `require_superadmin`): signature, expiry, audience, active account and `token_version` are checked on the server. A candidate's session token or a forged/expired token gets `401`; a recruiter calling an admin-only route gets `403`. `tests/test_admin_api.py` walks the OpenAPI route table and asserts that every admin route rejects unauthenticated calls.
5. Logout (`POST /admin/auth/logout`) increments `token_version`, which invalidates every token already issued. Changing a password, deactivating or changing the role of an account does the same.
6. Roles: `admin` (everything, incl. managing admin accounts and deleting data) and `recruiter` (candidates, jobs, interviews, notifications).

Create the first admin with `python -m scripts.create_admin --email you@company.com` (a Cloud Run Job in production; the password comes from an environment variable or a prompt, never the command line).

## 6. Candidate flow

1. Welcome -> consent (versioned) -> **application form**: name, e-mail, phone, location, experience, skills, position (from `GET /jobs`), resume (PDF/DOCX/TXT, validated by extension, size **and file signature**).
2. `POST /resume/upload`: text is extracted, the file is stored privately in Cloud Storage, a `Candidate` is created (`interview_status=applied`) and a secret `invite_token` returned.
3. `POST /session/start {candidate_id, invite_token}`: wrong token or unknown id both answer `403` (ids cannot be enumerated); `MAX_INTERVIEW_ATTEMPTS` is enforced; an attempt consumed by an AI outage is refunded.
4. Interview (voice, one turn at a time) -> the recording is uploaded **directly to the private bucket** with a signed URL (Cloud Run request bodies are limited to 32 MiB) and confirmed with `POST /session/{id}/video/complete`.
5. Invitation links sent by recruiters look like `https://frontend/?invite=<token>`: the app resolves the token, removes it from the address bar and skips the resume step.

## 7. AI interviewer flow

```
start -> load candidate profile + resume + JOB DESCRIPTION + settings -> system prompt
      -> Gemini (Vertex AI, structured output = TURN_SCHEMA) : opening question
loop  -> candidate answer (Whisper) -> Gemini gets: job description, candidate info, resume profile/text,
         recent conversation, latest answer, interview state (time, turn count, topics covered, facts learned),
         settings -> returns {learned, topic, move, end_interview, spoken_text}
         move in {opening, follow_up, new_resume_topic, situational, clarify, closing}  (chosen by the model)
      -> TTS (Piper) -> candidate hears it
end   -> time limit / turn limit / candidate ends / model decides -> status finished|ended_early
      -> background: final evaluation (Gemini, EVALUATION_SCHEMA) -> evaluations row, candidate.interview_score
      -> background: completion notification (e-mail/SMS) via notification-service
```

There is no question list anywhere; contact details (e-mail/phone) are never put in the prompt. The evaluation is decision support for a human (strengths/concerns must cite evidence; no inference about emotion, honesty or protected traits) and is never shown to the candidate.

## 8. Notifications

`notification_client.notify_candidate()` (backend) -> `POST {NOTIFICATION_SERVICE_URL}/v1/send` with `{channel, kind, to, context}` -> the notification service renders the template and calls the provider behind `EmailProvider` / `SmsProvider` interfaces (`console`, `smtp`, `sendgrid`; `console`, `twilio`). Kinds: `invitation`, `reminder`, `completion`, `status_update`. Every attempt is stored in `notification_records` (`sent` / `failed` / `skipped`); a provider outage never breaks the interview or admin request. Adding a vendor = one class in `notification-service/app/providers/`.

## 9. Security summary

* No secret in Git: `.env` files are ignored; `.env.example` has placeholders only; production values come from Secret Manager; there is no API-key setting for Gemini (ADC).
* Admin: bcrypt, JWT with server-side invalidation, role checks, login throttling, `/docs` and OpenAPI disabled in production.
* Candidates: per-candidate invite token + per-interview session token (constant-time compare).
* Files: private bucket (public access prevention), 10-minute signed URLs, `Cache-Control: no-store`, file-signature validation, size caps, signed uploads limited with `x-goog-content-length-range`.
* CORS: explicit frontend origins, explicit methods/headers, no credentials. nginx adds CSP, `X-Frame-Options`, `nosniff`, a restrictive Permissions-Policy.
* Logs: structured JSON, no request bodies, no tokens/passwords/e-mails/phone numbers, model output is not logged.
* Production start-up refuses unsafe settings (see `config.py`).
