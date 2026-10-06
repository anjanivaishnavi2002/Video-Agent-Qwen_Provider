# Running and deploying the platform

> Nothing in this repository deploys anything by itself. `infra/cloudrun/setup.sh` and `deploy.sh` are scripts **you** run, after reading them.

## 1. Run locally (no Docker)

Prerequisites: Python 3.12, Node 22, PostgreSQL 14+, `gcloud` (for Gemini).

```bash
# --- Gemini through Vertex AI, using your own Google login (no API key) ---
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com --project YOUR_PROJECT

# --- backend ---
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # set GOOGLE_CLOUD_PROJECT, DATABASE_URL, JWT_SECRET (python -c "import secrets;print(secrets.token_urlsafe(48))")
alembic upgrade head        # creates / upgrades the schema (existing data is kept)
python -m scripts.create_admin --email you@example.com   # prompts for a password
python scripts/download_piper_voice.py en_US-lessac-medium voices    # first time only (text-to-speech voice)
uvicorn app.main:app --reload --port 8000

# --- notification service (optional; without it notifications are recorded as "skipped") ---
cd ../notification-service && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && cp .env.example .env
uvicorn app.main:app --port 8081                          # EMAIL_PROVIDER=console just logs messages

# --- frontend ---
cd ../frontend && npm ci && npm run dev                   # http://localhost:5173  (admin: /admin/login)
```

No Vertex AI access yet? `LLM_PROVIDER=ollama` (with `OLLAMA_HOST`/`OLLAMA_MODEL`) runs the interviewer on a local Qwen model, for offline development only.

Tests: `cd backend && python -m pytest tests -q` (add `MIGRATION_TEST_DATABASE_URL=...` to also test the migrations on a scratch PostgreSQL), `cd notification-service && python -m pytest tests -q`, `cd frontend && npm run lint && npm test && npm run build`.

## 2. Run with Docker (local)

```bash
gcloud auth application-default login
cp backend/.env.example .env      # set GOOGLE_CLOUD_PROJECT and JWT_SECRET
docker compose -f docker-compose.dev.yml up --build
docker compose -f docker-compose.dev.yml run --rm backend python -m scripts.create_admin --email you@example.com
```

Frontend http://localhost:8080 (admin: `/admin/login`), API http://localhost:8000, notification service http://localhost:8081 (console providers). On Windows set `GCLOUD_ADC_DIR=%APPDATA%\gcloud`. Single images:

```bash
docker build -t interview-backend       backend
docker build -t interview-notification  notification-service
docker build -t interview-frontend      frontend
docker run --rm -p 8080:8080 -e PORT=8080 --env-file .env interview-backend     # listens on $PORT
```

`docker-compose.yml` (+ `.local`, `.gpu`, `.poc`, `.vertex`) and `.github/workflows/deploy.yml`, `infra/gcp/*`, `DEPLOY_GCP.md` describe the **earlier single-VM / Qwen deployment** and are kept only for reference.

## 3. Google Cloud setup

Replace the placeholders; `source infra/cloudrun/env.sh` (copy it to `env.local.sh` first) defines the variables used below.

```bash
export PROJECT_ID=my-project REGION=us-central1
gcloud config set project $PROJECT_ID
```

**Required APIs**: `run`, `artifactregistry`, `cloudbuild`, `aiplatform`, `sqladmin`, `secretmanager`, `storage`, `iam`, `iamcredentials`, `logging`, `monitoring` (+ `servicenetworking`, `compute` for private-IP Cloud SQL).

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  aiplatform.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com storage.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com logging.googleapis.com monitoring.googleapis.com
```

The whole one-time setup (APIs, service accounts, IAM, Artifact Registry, bucket, Cloud SQL, secrets) is in `infra/cloudrun/setup.sh`. The pieces:

### IAM (service accounts, least privilege)

| Service account | Roles |
|---|---|
| `interview-backend@` | `roles/aiplatform.user` (Gemini), `roles/cloudsql.client`, `roles/storage.objectAdmin` **on the bucket only**, `roles/secretmanager.secretAccessor` **on its three secrets**, `roles/iam.serviceAccountTokenCreator` **on itself** (V4 signed URLs without a key file), `roles/run.invoker` **on notification-service**, `roles/logging.logWriter`, `roles/monitoring.metricWriter` |
| `interview-notify@` | `roles/secretmanager.secretAccessor` (its secrets), `roles/logging.logWriter` |
| `interview-frontend@` | `roles/logging.logWriter` |

The deployer needs `roles/run.admin`, `roles/iam.serviceAccountUser` (on the three accounts), `roles/cloudbuild.builds.editor`, `roles/artifactregistry.writer`.

### Vertex AI (Gemini)

* Enable `aiplatform.googleapis.com`; grant the backend account `roles/aiplatform.user`. No model deployment, no endpoint, no key.
* **Gemini Live (voice interview):** `INTERVIEW_MODE=live`, `GEMINI_LIVE_MODEL` (default `gemini-3.8-live`), `GEMINI_LIVE_LOCATION` (default `us-central1`; the Live API is not available in every region), `GEMINI_LIVE_VOICE`. The browser opens a WebSocket to `/session/{id}/live`; the backend relays audio to Gemini, so credentials never reach the browser. Cloud Run needs `--timeout 3600` and session affinity (set in `deploy.sh`). Text models (`VERTEX_AI_MODEL`) are still used for resume analysis, scoring and summaries.
* Environment: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` (e.g. `us-central1`), `VERTEX_AI_MODEL` (e.g. `gemini-2.5-flash`; change the model without code changes), optional `LLM_FALLBACK_MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_TOKENS` (thinking tokens count toward it), `LLM_THINKING_BUDGET` / `LLM_THINKING_LEVEL` for models that support them, `LLM_SEND_SAMPLING_PARAMS=false` for models that deprecate temperature/top_p.
* Check the model name/region at <https://cloud.google.com/vertex-ai/generative-ai/docs/learn/models> - model availability differs per region and changes over time.
* Verify end to end after deployment (admin token needed): `GET /admin/system/llm-check` makes one tiny real call and reports credentials/model/region problems in plain words. `/health` and `/ready` never call Gemini.

### Cloud Storage

```bash
gcloud storage buckets create gs://$BUCKET --location $REGION --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://$BUCKET --lifecycle-file=lifecycle.json      # e.g. delete after DATA_RETENTION_DAYS
sed "s#FRONTEND_ORIGIN#https://YOUR-FRONTEND-URL#" infra/cloudrun/bucket-cors.json > /tmp/cors.json
gcloud storage buckets update gs://$BUCKET --cors-file=/tmp/cors.json            # needed for direct recording uploads
```

Settings: `STORAGE_BACKEND=gcs`, `GCS_BUCKET_NAME`, `GCS_PREFIX`, `GCS_SIGNING_SERVICE_ACCOUNT` (the backend account e-mail), `SIGNED_URL_TTL_SECONDS`. Objects stay private; admins get 10-minute signed URLs; PostgreSQL holds only `gs://...` references.

### Cloud SQL for PostgreSQL

```bash
gcloud sql instances create interview-db --database-version POSTGRES_16 --region $REGION \
  --tier db-custom-1-3840 --storage-size 10 --storage-auto-increase \
  --backup-start-time 02:00 --enable-point-in-time-recovery --deletion-protection
gcloud sql databases create interview --instance interview-db
gcloud sql users create interview_app --instance interview-db --password "$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
```

Cloud Run connects through the built-in Cloud SQL connector (`--add-cloudsql-instances PROJECT:REGION:interview-db`), i.e. a unix socket - no public authorised networks needed. `DATABASE_URL` (stored in Secret Manager as the whole string):

```
postgresql+psycopg2://interview_app:PASSWORD@/interview?host=/cloudsql/PROJECT:REGION:interview-db
```

Hardening options: private IP + Direct VPC egress (`--network/--subnet --vpc-egress private-ranges-only`, `servicenetworking` peering) and `--no-assign-ip`. Pool sizing: `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` per instance x `--max-instances` must stay under the instance's connection limit.

Migrations run as a **Cloud Run Job** (`alembic upgrade head`) before each deployment - never from several serving instances at once. `RUN_MIGRATIONS=true` exists for local Docker only. Take an on-demand backup first when a release contains a migration: `gcloud sql backups create --instance interview-db`.

### Secret Manager

| Secret | Consumed as | Used by |
|---|---|---|
| `database-url` | `DATABASE_URL` | backend, migration/admin jobs |
| `jwt-secret` | `JWT_SECRET` | backend |
| `notification-token` | `NOTIFICATION_SERVICE_TOKEN` (backend) / `NOTIFICATION_API_TOKEN` (notification service) | both |
| `sendgrid-api-key` / `smtp-password` | `SENDGRID_API_KEY` / `SMTP_PASSWORD` | notification service |
| `twilio-auth-token` | `TWILIO_AUTH_TOKEN` | notification service |
| `admin-bootstrap-password` | `ADMIN_PASSWORD` for the one-off create-admin job | job |

```bash
printf '%s' "$VALUE" | gcloud secrets create NAME --replication-policy automatic --data-file=-
gcloud secrets add-iam-policy-binding NAME --member serviceAccount:SA_EMAIL --role roles/secretmanager.secretAccessor
# in `gcloud run deploy`:  --set-secrets "ENV_NAME=NAME:latest"
```

Rotation: add a new version (`gcloud secrets versions add`), then redeploy/restart the service. Rotating `jwt-secret` signs every admin out.

### E-mail and SMS (notification service)

Choose providers with environment variables on `notification-service` (credentials from Secret Manager):

| Setting | Values |
|---|---|
| `EMAIL_PROVIDER` | `console` (log only) / `smtp` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`) / `sendgrid` (`SENDGRID_API_KEY`) |
| `EMAIL_FROM` | `Recruiting <recruiting@your-domain.com>` (a verified sender / domain with SPF + DKIM) |
| `SMS_PROVIDER` | `console` / `twilio` (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` or `TWILIO_MESSAGING_SERVICE_SID`) |
| `VOICE_PROVIDER` | `console` / `twilio` - automated phone calls read the message aloud (text-to-speech). Reuses the `TWILIO_*` credentials; set `TWILIO_VOICE_FROM_NUMBER` (a voice-capable Twilio number) and `VOICE_LANGUAGE` (default `en-IN`). Twilio trial accounts can only call verified numbers. |
| `NOTIFICATION_API_TOKEN` | shared secret; the backend sends the same value |
| `REQUIRE_TOKEN` | `true` (default). The service is also deployed with `--no-allow-unauthenticated`; the backend adds a Google ID token (`NOTIFICATION_AUTH_MODE=both`). |

The service refuses to start with an incomplete provider configuration. SMS to India etc. needs sender registration (DLT/10DLC) with the provider - a provider-side requirement, not a code one.

## 4. Build, push and deploy to Cloud Run

```bash
source infra/cloudrun/env.local.sh
gcloud artifacts repositories create $AR_REPO --repository-format docker --location $REGION   # once (setup.sh)

# Build + push (Cloud Build; or `docker build` + `docker push` to $REGISTRY/...)
gcloud builds submit backend              --tag $REGISTRY/backend:$TAG --timeout 3000s
gcloud builds submit notification-service --tag $REGISTRY/notification:$TAG
gcloud builds submit frontend             --tag $REGISTRY/frontend:$TAG

# 1) notification-service - private
gcloud run deploy notification-service --image $REGISTRY/notification:$TAG --region $REGION \
  --service-account $NOTIFY_SA --no-allow-unauthenticated --cpu 1 --memory 256Mi --max-instances 3 \
  --set-env-vars ENVIRONMENT=production,LOG_FORMAT=json,EMAIL_PROVIDER=sendgrid,EMAIL_FROM="Recruiting <recruiting@your-domain.com>",SMS_PROVIDER=twilio,VOICE_PROVIDER=twilio,REQUIRE_TOKEN=true \
  --set-secrets NOTIFICATION_API_TOKEN=notification-token:latest,SENDGRID_API_KEY=sendgrid-api-key:latest,TWILIO_AUTH_TOKEN=twilio-auth-token:latest
gcloud run services add-iam-policy-binding notification-service --region $REGION \
  --member serviceAccount:$BACKEND_SA --role roles/run.invoker

# 2) database migration job, then the backend
gcloud run jobs deploy interview-migrate --image $REGISTRY/backend:$TAG --region $REGION --service-account $BACKEND_SA \
  --add-cloudsql-instances $PROJECT_ID:$REGION:$SQL_INSTANCE --set-secrets DATABASE_URL=database-url:latest,JWT_SECRET=jwt-secret:latest \
  --set-env-vars ENVIRONMENT=production,STORAGE_BACKEND=gcs,GCS_BUCKET_NAME=$BUCKET,CORS_ORIGINS=https://placeholder.example \
  --command alembic --args upgrade,head --max-retries 0
gcloud run jobs execute interview-migrate --region $REGION --wait

gcloud run deploy interview-backend --image $REGISTRY/backend:$TAG --region $REGION --service-account $BACKEND_SA \
  --allow-unauthenticated --cpu 2 --memory 4Gi --concurrency 8 --timeout 300 \
  --min-instances 1 --max-instances 3 --no-cpu-throttling --cpu-boost --session-affinity \
  --add-cloudsql-instances $PROJECT_ID:$REGION:$SQL_INSTANCE \
  --set-env-vars ENVIRONMENT=production,LOG_FORMAT=json,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,VERTEX_AI_MODEL=gemini-2.5-flash,STORAGE_BACKEND=gcs,GCS_BUCKET_NAME=$BUCKET,GCS_SIGNING_SERVICE_ACCOUNT=$BACKEND_SA,NOTIFICATION_SERVICE_URL=$NOTIFY_URL,NOTIFICATION_AUTH_MODE=both,PRELOAD_MODELS=true,CORS_ORIGINS=https://placeholder.example,FRONTEND_BASE_URL=https://placeholder.example \
  --set-secrets DATABASE_URL=database-url:latest,JWT_SECRET=jwt-secret:latest,NOTIFICATION_SERVICE_TOKEN=notification-token:latest

# 3) frontend (API_URL is injected at start-up; the same image works everywhere)
gcloud run deploy interview-frontend --image $REGISTRY/frontend:$TAG --region $REGION --service-account $FRONTEND_SA \
  --allow-unauthenticated --cpu 1 --memory 256Mi --max-instances 5 \
  --set-env-vars API_URL=$(gcloud run services describe interview-backend --region $REGION --format 'value(status.url)')

# 4) tell the backend the real frontend origin (CORS + e-mail links)
FRONTEND_URL=$(gcloud run services describe interview-frontend --region $REGION --format 'value(status.url)')
gcloud run services update interview-backend --region $REGION \
  --update-env-vars "^@^CORS_ORIGINS=$FRONTEND_URL@FRONTEND_BASE_URL=$FRONTEND_URL"
```

`infra/cloudrun/deploy.sh` runs this whole sequence (including the bucket CORS and first-admin instructions). With a custom domain (`gcloud beta run domain-mappings` or a load balancer) use that origin for `CORS_ORIGINS`, `FRONTEND_BASE_URL` and the bucket CORS file.

### Cloud Run settings explained

* **`--no-cpu-throttling`**: the final evaluation, summary and notifications run in background tasks *after* the HTTP response; without always-allocated CPU they would stall.
* **`--session-affinity`, `--min-instances 1`, `--max-instances 3`**: live interview state is held in memory (and rebuilt from PostgreSQL if an instance restarts mid-interview). Affinity keeps a candidate on one instance; keep the maximum small until the speech models move out of the container (see TODO).
* **`--memory 4Gi --cpu 2`**: the container runs Whisper (speech-to-text) and Piper (text-to-speech). `PRELOAD_MODELS=true` + `--cpu-boost` avoid a slow first answer.
* The backend listens on `$PORT` (default 8080), runs as a non-root user, one worker, handles `SIGTERM` gracefully (`--timeout-graceful-shutdown`).
* **Health**: `GET /health` = liveness (touches nothing external). `GET /ready` = readiness (database + configuration; never calls Gemini). Add an uptime check on `/health`:
  `gcloud monitoring uptime create interview-backend-health --resource-type=uptime-url --resource-labels=host=BACKEND_HOST,project_id=$PROJECT_ID --path=/health` (or create it in the console).

### First admin

```bash
printf '%s' 'a-long-random-password' | gcloud secrets create admin-bootstrap-password --data-file=-
gcloud secrets add-iam-policy-binding admin-bootstrap-password --member serviceAccount:$BACKEND_SA --role roles/secretmanager.secretAccessor
gcloud run jobs deploy interview-create-admin --image $REGISTRY/backend:$TAG --region $REGION --service-account $BACKEND_SA \
  --add-cloudsql-instances $PROJECT_ID:$REGION:$SQL_INSTANCE \
  --set-secrets DATABASE_URL=database-url:latest,JWT_SECRET=jwt-secret:latest,ADMIN_PASSWORD=admin-bootstrap-password:latest \
  --set-env-vars ENVIRONMENT=production,STORAGE_BACKEND=gcs,GCS_BUCKET_NAME=$BUCKET,CORS_ORIGINS=$FRONTEND_URL \
  --command python --args=-m,scripts.create_admin,--email,you@your-company.com,--role,admin
gcloud run jobs execute interview-create-admin --region $REGION --wait
gcloud secrets delete admin-bootstrap-password      # then sign in at $FRONTEND_URL/admin/login and change the password
```

## 5. Environment variable reference

| Variable | Where | Meaning |
|---|---|---|
| `ENVIRONMENT` | backend | `production` turns on the safety checks |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `VERTEX_AI_MODEL` | backend | Vertex AI / Gemini |
| `DATABASE_URL` | backend (secret) | PostgreSQL URL (Cloud SQL socket form above) |
| `AUTO_CREATE_SCHEMA` | backend | dev only; must be `false` in production |
| `JWT_SECRET`, `ADMIN_TOKEN_EXPIRE_MINUTES` | backend (secret) | admin tokens |
| `STORAGE_BACKEND`, `GCS_BUCKET_NAME`, `GCS_PREFIX`, `GCS_SIGNING_SERVICE_ACCOUNT` | backend | file storage |
| `CORS_ORIGINS`, `FRONTEND_BASE_URL` | backend | the frontend origin |
| `NOTIFICATION_SERVICE_URL`, `NOTIFICATION_SERVICE_TOKEN`, `NOTIFICATION_AUTH_MODE` | backend | notification client |
| `MAX_INTERVIEW_ATTEMPTS`, `INTERVIEW_DURATION_MINUTES`, `MAX_TURNS`, `INTERVIEWER_NAME`, ... | backend | interview behaviour (unchanged) |
| `EMAIL_PROVIDER`, `EMAIL_FROM`, `SMTP_*`, `SENDGRID_API_KEY`, `SMS_PROVIDER`, `TWILIO_*`, `NOTIFICATION_API_TOKEN` | notification-service | providers |
| `API_URL`, `PORT` | frontend | backend URL (written to `/config.json` at start-up) |

Full annotated list: `backend/.env.example`, `notification-service/.env.example`.

## 6. Operating notes

* **Logs**: JSON on stdout -> Cloud Logging (`severity`, `message`). Useful filter: `resource.type="cloud_run_revision" severity>=WARNING`. Create log-based alerts for `severity>=ERROR` and for the message `Evaluation of interview` (failed evaluations can be re-run from the admin UI).
* **Monitoring**: Cloud Run request latency/5xx/instance count dashboards, Cloud SQL CPU/connections/storage, an uptime check on `/health`, an alert on notification `failed` records (visible on the admin dashboard).
* **Rollback**: `gcloud run services update-traffic interview-backend --to-revisions REVISION=100`. Migrations are additive, so the previous revision keeps working after a migration.
* **Privacy**: candidates, their files and notification history can be deleted by an admin (`DELETE /admin/candidates/{id}` removes DB rows and the stored objects); the bucket lifecycle rule enforces the retention period.

## 7. Remaining TODOs / blockers

Things that **could not be verified in the build environment** (no Google Cloud project, no Docker daemon): real Vertex AI calls (the provider is covered by tests with a mocked SDK client), the Docker image builds (written to standard practice; `frontend` and `notification-service` images are small and built in CI, the backend image downloads speech models and is built by Cloud Build), Cloud Run deployment, signed-URL signing with real IAM, SMTP/SendGrid/Twilio delivery, and the bucket CORS/PUT upload from a real browser. Run the checklist below on first deployment.

1. Revoke the Gemini API key that was present in the old `.env.example` and make sure no copy of `backend/.env` was ever committed (`git log -p -- '*.env*'`).
2. First-deployment smoke test: `/health` -> `/ready` -> admin login -> `/admin/system/llm-check` -> create a job -> apply as a candidate -> run a short interview -> see the recording, transcript and AI evaluation in the admin UI -> send an invitation.
3. **Speech models inside the backend container** (Whisper + Piper) make the image large and the instance heavy (4 GiB, in-memory sessions, affinity). Recommended next step: Google Cloud Speech-to-Text and Text-to-Speech, which would make the backend stateless and lighter (`--max-instances` could then be raised freely). Not done because it changes the audio pipeline and voice quality - decide first.
4. In-memory interview sessions are per instance (restored from the database after a restart). With more than one instance, a candidate double-submitting across instances is not serialised; affinity makes this unlikely.
5. Rate limiting for public endpoints (`/resume/upload`, `/session/start`) and login is per instance only. Add Cloud Armor (behind a load balancer) for real protection and CAPTCHA if you expect abuse.
6. Admin login has no MFA. Consider Identity-Aware Proxy in front of `/admin`, or Google/OIDC sign-in, for stronger admin identity.
7. SMS/e-mail sender registration and domain verification (SPF/DKIM/DMARC; DLT/10DLC) are provider-side tasks.
8. Optional hardening: private-IP Cloud SQL + Direct VPC egress, VPC Service Controls, CMEK for the bucket/database, Binary Authorization, Cloud Build trigger instead of manual builds.
9. Existing old `uploads/` resumes and recordings on the VM are not migrated to the bucket automatically (copy with `gcloud storage cp -r` and update `resume_path`/`video_path` if you need the history).
