# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache

# libgomp: needed by onnxruntime / ctranslate2
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the speech models into the image so containers start without downloading.
ARG WHISPER_MODEL=base
ARG PIPER_VOICE=en_US-lessac-medium
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')"
COPY scripts ./scripts
RUN python scripts/download_piper_voice.py "${PIPER_VOICE}" voices

COPY app ./app

# Run as a non-root user. /data holds temporary uploads (and local files if STORAGE_BACKEND=local).
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data /opt/hf-cache /app
USER appuser

ENV UPLOAD_DIR=/data/uploads \
    PIPER_VOICE_PATH=voices/en_US-lessac-medium.onnx

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

# ONE worker on purpose: interview sessions are held in memory (they are also
# restored from the database after a restart). Scale vertically, or add sticky routing first.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
