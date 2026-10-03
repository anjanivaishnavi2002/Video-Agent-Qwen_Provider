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
