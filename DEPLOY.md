# Deploy: backend on Render, frontend on Netlify

## 1. Backend (Render)
1. Render dashboard -> New -> **Blueprint** -> pick this GitHub repo (it reads `render.yaml`).
   It creates the web service `video-agent-api` and a PostgreSQL database.
2. When asked, set the two secret values:
   - `GEMINI_API_KEY` - your Gemini API key (never commit it).
   - `CORS_ORIGINS` - your Netlify URL, e.g. `https://your-site.netlify.app` (no trailing slash). Several URLs: comma-separated.
3. After the deploy, open `https://<your-service>.onrender.com/health?deep=true` - it should report the model as OK.

## 2. Frontend (Netlify)
1. Netlify -> Add new site -> Import from Git -> this repo. `netlify.toml` sets the build.
2. Site settings -> Environment variables -> add `VITE_API_URL` = `https://<your-service>.onrender.com` (no trailing slash), then redeploy.
3. Put the final Netlify URL into Render's `CORS_ORIGINS` (step 1.2) if you have not already.

Netlify cannot proxy WebSockets, so the browser talks to Render directly (that is why CORS_ORIGINS is needed).

## Notes
- Free Render services sleep after ~15 min idle: wake it by opening /healthz before a demo, or use the Starter plan.
- Render's disk is ephemeral: uploaded resumes/recordings vanish on redeploy. For a POC this is fine; for keeping them set
  `STORAGE_BACKEND=gcs` + `GCS_BUCKET` (and Google credentials), or add a Render persistent disk (paid).
- The Render build skips the local Whisper/Piper speech models (`requirements-render.txt`); the live interview uses Gemini.
- Camera and microphone need HTTPS - both Netlify and Render provide it.
