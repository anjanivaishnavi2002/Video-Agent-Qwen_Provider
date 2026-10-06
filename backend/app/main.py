import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import candidates as candidates_api
from app.api import config as config_api
from app.api import live as live_api
from app.api import resume, session, voice
from app.api.admin import router as admin_router
from app.config import settings
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal, engine, init_db
from app.services import voice_service
from app.services.admin_service import ensure_bootstrap_admin

setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = logging.getLogger(__name__)


def _warm_up_models() -> None:
    """Load Whisper + Piper in the background so the first answer isn't slow."""
    try:
        voice_service.warm_up()
        logger.info("Voice models loaded.")
    except Exception as exc:  # never crash the server because of warm-up
        logger.warning("Voice model warm-up failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    for sub in (settings.RESUME_SUBDIR, settings.VIDEO_SUBDIR):
        Path(settings.UPLOAD_DIR, sub).mkdir(parents=True, exist_ok=True)
    try:
        with SessionLocal() as db:
            ensure_bootstrap_admin(db)
    except Exception:
        logger.exception("Could not create the bootstrap admin (has `alembic upgrade head` been run?)")
    if settings.PRELOAD_MODELS:
        threading.Thread(target=_warm_up_models, daemon=True).start()
    logger.info("Started: environment=%s llm=%s model=%s storage=%s", settings.ENVIRONMENT, settings.llm_backend,
                settings.active_model, settings.STORAGE_BACKEND)
    yield
    engine.dispose()        # graceful shutdown: return database connections
    logger.info("Stopped.")


app = FastAPI(
    title="AI Video Interview Platform", lifespan=lifespan,
    # The interactive docs list every admin route: keep them off in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None, openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,      # explicit frontend origins only
    allow_credentials=False,                       # bearer tokens in headers, no cookies
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Session-Token", "X-API-Key"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(config_api.router)
app.include_router(candidates_api.router)
app.include_router(resume.router)
app.include_router(session.router)
app.include_router(live_api.router)
app.include_router(admin_router)
if settings.ENABLE_DEBUG_ENDPOINTS:  # raw STT/TTS: local debugging only
    app.include_router(voice.router)


@app.get("/")
def root():
    return {"message": "AI Video Interview Platform API"}


@app.get("/health")
@app.get("/healthz")
def health():
    """Liveness: the process is up. Deliberately touches nothing external (no database, no Gemini)."""
    return {"status": "healthy"}


@app.get("/ready")
def ready():
    """Readiness: can this instance serve traffic? Checks configuration and the database - never calls Gemini."""
    checks: dict = {}
    checks["llm_configured"] = bool(settings.active_model) and (
        settings.llm_backend == "ollama" or bool(settings.GOOGLE_CLOUD_PROJECT))
    checks["admin_auth_configured"] = len(settings.JWT_SECRET) >= 32
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False
    ok = all(checks.values())
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", "checks": checks})
