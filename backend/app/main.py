import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import config as config_api
from app.api import resume, session, voice
from app.config import settings
from app.db.database import init_db
from app.services import voice_service

init_db()

for sub in (settings.RESUME_SUBDIR, settings.VIDEO_SUBDIR):
    Path(settings.UPLOAD_DIR, sub).mkdir(parents=True, exist_ok=True)


def _warm_up_models() -> None:
    """Load Whisper + Piper in the background so the first answer isn't slow."""
    try:
        voice_service.warm_up()
        print("Voice models loaded.")
    except Exception as exc:  # never crash the server because of warm-up
        print(f"Voice model warm-up failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.PRELOAD_MODELS:
        threading.Thread(target=_warm_up_models, daemon=True).start()
    yield


app = FastAPI(title="AI Video Interview Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_api.router)
app.include_router(resume.router)
app.include_router(session.router)
if settings.ENABLE_DEBUG_ENDPOINTS:  # raw STT/TTS: local debugging only
    app.include_router(voice.router)


@app.get("/")
def root():
    return {"message": "AI Video Interview Agent is running"}


@app.get("/healthz")
def healthz():
    """Cheap liveness probe for Docker / load balancers (does not call the LLM)."""
    return {"status": "ok"}


@app.get("/health")
def health(deep: bool = False):
    """
    Which LLM back end is configured, and is it usable?

    Plain call: configuration only (no cost). `?deep=true`: also makes one tiny real model call,
    which proves the credentials, project/location, model id and network path end to end.
    """
    provider = settings.LLM_PROVIDER.strip().lower()
    result: dict = {
        "status": "ok",
        "llm_provider": provider,
        "llm_model": settings.active_model,
        "live_model": settings.GEMINI_LIVE_MODEL,
    }
    if provider == "vertex":
        result["vertex_project"] = settings.VERTEX_PROJECT_ID or None
        result["vertex_location"] = settings.VERTEX_LOCATION
        result["configured"] = bool(settings.VERTEX_PROJECT_ID)   # auth = the VM's service account
    else:
        result["configured"] = bool(settings.GEMINI_API_KEY)      # the key itself is never returned
    if not result["configured"]:
        result["status"] = "llm_not_configured"
    elif deep:
        from app.providers.gemini_provider import get_llm
        from app.providers.llm_errors import LLMError

        try:
            result["llm_check"] = get_llm().ping()
        except LLMError as exc:
            result["status"] = exc.code
            result["llm_check"] = {"ok": False, "error": exc.message}
    return result
