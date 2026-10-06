# syntax=docker/dockerfile:1.7
# Interview backend (FastAPI). Built for Cloud Run: listens on $PORT, runs as a non-root user, no dev server.

# ---------- stage 1: build the Python environment ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the speech models into the image so instances start without downloading anything.
ARG WHISPER_MODEL=base
ARG PIPER_VOICE=en_US-lessac-medium
ENV HF_HOME=/opt/hf-cache
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')"
COPY scripts/download_piper_voice.py ./scripts/download_piper_voice.py
RUN python scripts/download_piper_voice.py "${PIPER_VOICE}" /opt/voices

# ---------- stage 2: small runtime image ----------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/opt/hf-cache \
    PORT=8080 \
    UPLOAD_DIR=/tmp/uploads \
    PIPER_VOICE_PATH=/opt/voices/en_US-lessac-medium.onnx \
    LOG_FORMAT=json

# libgomp: needed by onnxruntime / ctranslate2
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf-cache /opt/hf-cache
COPY --from=builder /opt/voices /opt/voices
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts ./scripts
COPY docker-entrypoint.sh .
# /tmp is the only writable place on Cloud Run: uploads are staged there, then moved to Cloud Storage.
RUN mkdir -p /tmp/uploads && chmod +x docker-entrypoint.sh && chown -R app:app /app /tmp/uploads
USER 10001

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8080'), timeout=4).status==200 else 1)"

# `exec` inside the entrypoint makes uvicorn PID 1: it gets SIGTERM from Cloud Run and shuts down gracefully.
ENTRYPOINT ["./docker-entrypoint.sh"]
