"""
Notification service: its own Cloud Run service. The interview backend calls POST /v1/send; nothing here knows
about interviews, candidates' data or Gemini.
"""
import hmac
import json
import logging
import re
import sys
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.providers.base import ProviderError
from app.providers.email import build_email_provider, mask
from app.providers.sms import build_sms_provider
from app.providers.voice import build_voice_provider
from app.templates import KINDS, render_email, render_sms, render_voice


class _Json(logging.Formatter):
    def format(self, record):
        out = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(_Json() if settings.LOG_FORMAT == "json" else
                     logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.basicConfig(level=settings.LOG_LEVEL.upper(), handlers=[handler], force=True)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("notification-service")


@lru_cache(maxsize=1)
def email_provider():
    return build_email_provider(settings)


@lru_cache(maxsize=1)
def sms_provider():
    return build_sms_provider(settings)


@lru_cache
def voice_provider():
    return build_voice_provider(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast on a mis-configured provider instead of failing on the first candidate.
    email_provider(), sms_provider(), voice_provider()
    logger.info("Started: email=%s sms=%s", email_provider().name, sms_provider().name)
    yield


app = FastAPI(title="Notification Service", lifespan=lifespan,
              docs_url=None if settings.ENVIRONMENT == "production" else "/docs", redoc_url=None,
              openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json")


def require_caller(x_notification_token: str | None = Header(default=None)) -> None:
    if not settings.REQUIRE_TOKEN:
        return        # Cloud Run IAM (roles/run.invoker) is the only gate
    expected = settings.NOTIFICATION_API_TOKEN
    if not expected or not x_notification_token or not hmac.compare_digest(expected, x_notification_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


class SendIn(BaseModel):
    channel: str = Field(pattern="^(email|sms|call)$")
    kind: str
    to: str = Field(min_length=3, max_length=255)
    context: dict = Field(default_factory=dict)


_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{6,18}[0-9]$")


@app.get("/health")
@app.get("/healthz")
def health():
    return {"status": "healthy"}


@app.get("/ready")
def ready():
    ok = bool(settings.NOTIFICATION_API_TOKEN) or not settings.REQUIRE_TOKEN
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ready" if ok else "not_ready",
                                 "email_provider": settings.EMAIL_PROVIDER, "sms_provider": settings.SMS_PROVIDER,
                                 "voice_provider": settings.VOICE_PROVIDER})


@app.post("/v1/send", dependencies=[Depends(require_caller)])
def send(data: SendIn):
    if data.kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {list(KINDS)}")
    try:
        if data.channel == "email":
            if not _EMAIL.match(data.to):
                raise HTTPException(status_code=400, detail="Invalid e-mail address")
            subject, text, html = render_email(data.kind, data.context)
            result = email_provider().send(to=data.to, subject=subject, text=text, html=html)
        else:
            if not _PHONE.match(data.to):
                raise HTTPException(status_code=400, detail="Invalid phone number")
            if data.channel == "sms":
                result = sms_provider().send(to=data.to, body=render_sms(data.kind, data.context))
            else:
                result = voice_provider().call(to=data.to, message=render_voice(data.kind, data.context))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderError as exc:
        logger.warning("Send failed (%s/%s to %s): %s", data.channel, data.kind, mask(data.to), exc)
        return JSONResponse(status_code=502 if exc.retryable else 200,
                            content={"status": "failed", "error": str(exc), "retryable": exc.retryable})
    logger.info("Sent %s/%s to %s via %s", data.channel, data.kind, mask(data.to), result.provider)
    return {"status": "sent", "provider": result.provider, "message_id": result.message_id}
