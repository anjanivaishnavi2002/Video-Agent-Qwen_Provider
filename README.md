# AI Video Interview Agent (BPO)

A hands-free, voice-based AI interviewer. The candidate enters a name and uploads a resume;
the interviewer (Qwen 2.5 via Ollama) speaks (Piper), listens (browser silence detection), transcribes
(Faster-Whisper) and decides every next question from the **resume + conversation + BPO context**.
There are no fixed questions and no job selection. The camera + microphone are recorded for the whole
interview and observable face events (missing / returned / multiple faces / head movement) are logged.

```
Browser ──HTTPS──► web (Caddy: React app, /api proxy, Let's Encrypt)
                      └──► backend (FastAPI + Whisper + Piper) ──► ollama (Qwen 2.5)
                                  ├──► Cloud SQL (PostgreSQL)   via cloudsql-proxy
                                  └──► Cloud Storage (resumes, recordings)
```

## Repository layout

| Path | What |
|---|---|
| `backend/` | FastAPI app (`app/`), tests, `Dockerfile` |
| `frontend/` | React + Vite app, `Dockerfile`, `Caddyfile` |
| `docker-compose.yml` | Production stack for one VM (`web`, `backend`, `ollama`, `cloudsql-proxy`) |
| `docker-compose.local.yml` | Overlay: local Postgres, port 8080 – try everything without GCP |
| `docker-compose.gpu.yml` | Overlay: NVIDIA GPU for Ollama |
| `infra/gcp/` | `setup.sh` (one-time GCP provisioning), `vm-startup.sh` |
| `.github/workflows/` | `ci.yml` (tests on PRs), `deploy.yml` (build → Artifact Registry → VM) |
| `.env.example` | Deployment settings. `backend/.env.example` lists every tunable (interview length, silence timeout, tone, models…) |

Interview behaviour is data, not code: edit `backend/app/prompts/interviewer.yaml` (how to interview) and
`backend/app/prompts/bpo_context.yaml` (BPO guidance). Everything else is an environment variable.

## 1. Run locally without Docker (development)

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python scripts/download_piper_voice.py en_US-lessac-medium voices
cp .env.example .env                                     # set DATABASE_URL
ollama pull qwen2.5:3b-instruct
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend && npm install && npm run dev                # http://localhost:5173
```

Tests: `cd backend && python -m pytest tests -q` and `cd frontend && npm run lint && npm test`.

## 2. Run the whole stack in Docker locally

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
# wait until the one-shot `ollama-pull` container has exited (it downloads the Qwen model)
# open http://localhost:8080   ·   health: http://localhost:8080/api/health
```

## 3. Deploy to GCP (Compute Engine VM)

One-time:

1. Push this repo to GitHub.
2. `export PROJECT_ID=... GITHUB_REPO=owner/name DOMAIN=interview.example.com` and run `bash infra/gcp/setup.sh`
   (review it first – it creates billable resources: VM, Cloud SQL, bucket, Artifact Registry, service accounts,
   Workload Identity Federation for GitHub). For a GPU VM: `MACHINE_TYPE=g2-standard-8 GPU=nvidia-l4`.
3. Follow the printed instructions: DNS A record, GitHub secrets/variables, and the VM's `/opt/video-agent/.env`.

Every deploy afterwards: **push/merge to `main`** → tests → images built and pushed to Artifact Registry →
VM pulls and restarts → smoke test on `/api/healthz`.

Manual deploy on the VM:

```bash
cd /opt/video-agent
sudo docker compose --profile cloudsql pull && sudo docker compose --profile cloudsql up -d
sudo docker compose logs -f backend
```

### Why a VM and not Cloud Run
Whisper, Piper and Qwen need CPU/GPU and local model files, interviews are long-lived streams of requests, and live sessions are held in the backend's
memory. A VM running compose is the simplest thing that works. Run **one backend replica**.

## 4. Security model

* Starting an interview returns a random **session token**; the browser sends it as `X-Session-Token` on every later call. Without it all session endpoints return 403.
* Recruiters read results with `X-API-Key: $ADMIN_API_KEY` → `GET /api/session/<id>/result`.
* Raw `/voice/stt` and `/voice/tts` are disabled in production (`ENABLE_DEBUG_ENDPOINTS=false`).
* Resumes and recordings go to a private Cloud Storage bucket (public access prevention on); the VM's service account is the only credential.
* Secrets live in `/opt/video-agent/.env` on the VM, never in git. Candidate consent, data retention and privacy-law compliance (video + voice + CV are personal data) are **your responsibility** – add a consent screen and retention policy before real candidates use this.

## 5. Known limitations

* Not load-tested. CPU-only Qwen + Whisper is slow (several seconds per reply); use a GPU VM for real traffic.
* Live sessions are in memory (restored from the DB after a restart); do not run more than one backend replica.
* Recording uploads once at the end of the interview; a closed tab loses it. The AI's voice is not in the recording.
* Face monitoring downloads its model from Google at page load (or host it yourself: `FACE_MODEL_URL`).
* The candidate-facing endpoints (`/resume/upload`, `/session/start`) are unauthenticated and not rate limited.
