# Deploy on GCP (fully self-hosted: Whisper -> Qwen via Ollama -> Piper)

VM: `video-agent-cpu` (e2-standard-4, Ubuntu 22.04, asia-south1-a). Everything runs in Docker.

## 1. Firewall (once, from Cloud Shell or your PC)
```
gcloud compute firewall-rules create video-agent-web --allow tcp:80,tcp:443 --target-tags=http-server,https-server
gcloud compute instances add-tags video-agent-cpu --zone asia-south1-a --tags http-server,https-server
```

## 2. Get the code on the VM
```
cd ~/Video_Agent- && git pull origin main
```

## 3. Create `.env` in the repo folder
Camera/mic only work over HTTPS. `sslip.io` gives a free hostname for the IP, and Caddy gets the certificate automatically.
```
OLLAMA_MODEL=qwen2.5:3b-instruct
WHISPER_MODEL=base
POSTGRES_PASSWORD=ChangeMe123abc      # letters and digits only
ADMIN_API_KEY=<long random string>
SITE_ADDRESS=34-47-197-37.sslip.io
CORS_ORIGINS=https://34-47-197-37.sslip.io
```

## 4. Start
```
docker compose -f docker-compose.yml -f docker-compose.poc.yml up -d --build
docker compose logs -f ollama-pull      # wait for "success" (~2 GB download)
```

## 5. Check
Open `https://34-47-197-37.sslip.io/api/health?deep=true` -> should report ollama OK with the model present.
Then open `https://34-47-197-37.sslip.io` and run an interview.

## Notes
- CPU only: each AI reply takes roughly 3-10 s, so the interviewer pauses briefly after you speak. For faster replies use a GPU VM with `docker-compose.gpu.yml` added.
- Keep 20 GB+ disk free (images + models). Check with `df -h`.
- A 3B model is small: questions are simpler and scorecards are rougher than with a large hosted model. Try `qwen2.5:7b-instruct` on a bigger VM if quality matters.
- Update later: `git pull && docker compose -f docker-compose.yml -f docker-compose.poc.yml up -d --build`.
- Logs: `docker compose logs -f backend`.

## Option B: Qwen on Vertex AI (small VM, no model on the VM)

The VM only runs the app, Whisper and Piper; the LLM runs on Google Cloud. Replies are usually faster than CPU-only Qwen.

1. **Pick a model.** Console -> Vertex AI -> Model Garden -> search `Qwen`. Open a model, accept the terms / click *Enable*, and copy its **model id** and note the **region** it is offered in.
2. **Enable the API.** Console -> APIs & Services -> enable *Vertex AI API*.
3. **Let the VM call it.** Stop the VM -> Edit -> *Security and access* -> **Service account** (the default one is fine) and **Access scopes: Allow full access to all Cloud APIs** -> Save -> Start. Then Console -> IAM -> find that service account (`...-compute@developer.gserviceaccount.com`) -> add the role **Vertex AI User**.
4. **Add to `.env` on the VM** (`nano .env`):
   ```
   LLM_PROVIDER=vertex
   VERTEX_MODEL=<model id from Model Garden>
   VERTEX_LOCATION=<region of that model>
   ```
   If you *deployed* the model to your own endpoint, also set `VERTEX_ENDPOINT_ID=<endpoint id>`.
5. **Start without the Ollama containers:**
   ```
   docker compose -f docker-compose.yml -f docker-compose.poc.yml -f docker-compose.vertex.yml up -d --build
   ```
   If the Ollama containers were already running, stop them first: `docker compose -f docker-compose.yml -f docker-compose.poc.yml down`.
6. **Check:** `https://<site>/api/health?deep=true` should show `"llm_provider":"vertex"` and `"llm_check":{"ok":true,...}`. A 403 means the role, scope or model terms from steps 1-3 are missing; a 404 means a wrong `VERTEX_MODEL` / `VERTEX_LOCATION`.

Cost: Vertex bills per use (or per hour for your own endpoint). Candidate transcripts and resume text are sent to Google Cloud, so update the consent text if needed and have it reviewed.
