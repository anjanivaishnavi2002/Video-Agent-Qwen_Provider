# AI Video Interview Agent (BPO)

A hands-free voice interviewer that runs **fully self-hosted**. The candidate enters a name and uploads a resume.
The interviewer (Alex) then asks questions generated from the **resume + conversation + BPO context**; there are no
fixed questions. Each turn: the browser detects when the candidate stops speaking, the backend transcribes it
(Faster-Whisper), Qwen (served by Ollama) decides the next question, and Piper speaks it. The camera and microphone are
recorded for the whole interview and observable face events (missing / returned / multiple faces / head movement)
are logged. After the interview a summary and an advisory scorecard are produced.

```
Browser --HTTPS--> web (Caddy: React app, /api proxy, automatic HTTPS)
                      └--> backend (FastAPI + Faster-Whisper + Piper) --> ollama (Qwen 2.5)
                              ├--> PostgreSQL (container, or Cloud SQL via cloudsql-proxy)
                              └--> local disk (or a Cloud Storage bucket) for resumes and recordings
```

No audio, transcript or resume text is sent to any outside AI service.

## Repository layout

| Path | What |
|---|---|
| `backend/` | FastAPI app (`app/`), tests, `Dockerfile` |
| `frontend/` | React + Vite app, `Dockerfile`, `Caddyfile` |
| `docker-compose.yml` | Production stack for one VM (`web`, `backend`, `ollama`, `ollama-pull`, `cloudsql-proxy`) |
| `docker-compose.poc.yml` | Overlay: PostgreSQL container + local disk (single-VM proof of concept) |
| `docker-compose.local.yml` | Overlay: local Postgres, port 8080, to try everything on your own machine |
| `docker-compose.gpu.yml` | Overlay for a GPU VM (faster replies) |
| `DEPLOY_GCP.md` | Step-by-step deployment on the Compute Engine VM |
| `infra/gcp/` | `setup.sh` (one-time GCP provisioning), `vm-startup.sh` |
| `.github/workflows/` | `ci.yml` (tests), `deploy.yml` (build, push to Artifact Registry, deploy to the VM) |
| `.env.example` | Deployment settings. `backend/.env.example` lists every tunable (interview length, silence timeout, tone, model...) |

Interview behaviour is data, not code: edit `backend/app/prompts/interviewer.yaml` (how to interview) and
`backend/app/prompts/bpo_context.yaml` (BPO guidance). Everything else is an environment variable.

## 1. Run locally without Docker (development)

```bash
# Ollama: install it from ollama.com, then
ollama pull qwen2.5:3b-instruct

# backend
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env                                     # set DATABASE_URL
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend && npm install && npm run dev                # http://localhost:5173
```

Tests: `cd backend && python -m pytest tests -q` and `cd frontend && npm run lint && npm test`.

## 2. Run the whole stack in Docker locally

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
# open http://localhost:8080   -   health: http://localhost:8080/api/health?deep=true
```

## 3. Deploy to GCP

See `DEPLOY_GCP.md` (single VM proof of concept). For the full setup with Cloud SQL, a bucket and GitHub Actions
deploys, use `infra/gcp/setup.sh` and `.github/workflows/deploy.yml`. Run **one backend replica** (interview sessions
are held in memory and restored from the database after a restart).

## 4. Security model

* Starting an interview returns a random **session token**; the browser sends it as `X-Session-Token` on REST calls.
* Recruiters read results with `X-API-Key: $ADMIN_API_KEY` -> `GET /api/session/<id>/result`.
* Raw `/voice/stt` and `/voice/tts` are disabled in production (`ENABLE_DEBUG_ENDPOINTS=false`).
* Secrets live in the VM's `.env`, never in git. Consent, data retention and privacy-law compliance (video + voice + CV are personal data) are **your responsibility**: have legal/HR review the consent text and set a retention policy before real candidates use this.

## 5. Known limitations

* Not load-tested. On a CPU-only VM each reply takes a few seconds; use a GPU VM for faster replies.
* A 3B model gives simpler questions and rougher scorecards than a large model; try a larger Qwen if quality matters.
* Recording uploads once at the end of the interview; a closed tab loses it.
* Face monitoring downloads its MediaPipe model from a Google-hosted URL at page load (or host it yourself: `FACE_MODEL_URL`).
* The candidate-facing endpoints (`/resume/upload`, `/session/start`) are unauthenticated and not rate limited.
