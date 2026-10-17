"""LLM gateway: its own private Cloud Run service. The interview backend calls POST /v1/chat; this service holds the
provider API keys (OpenAI, Anthropic, Gemini API key, DeepSeek), spreads requests across them and fails over.
Message content is never logged."""
import hmac
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.router import AllProvidersFailed, NoProviders, Router, build_providers


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
logger = logging.getLogger("llm-gateway")

MAX_MESSAGES = 200
MAX_CHARS = 400_000


@lru_cache(maxsize=1)
def router() -> Router:
    return Router(build_providers(settings), settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # NOTE: a missing key never stops the service starting (that would block deploys); /v1/chat then answers 503.
    r = router()
    logger.info("Started: providers=%s weights=%s", list(r.providers) or "NONE CONFIGURED", dict(r._weights))
    yield


app = FastAPI(title="LLM Gateway", lifespan=lifespan, docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
              redoc_url=None, openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json")


def require_caller(x_gateway_token: str | None = Header(default=None)) -> None:
    if not settings.REQUIRE_TOKEN:
        return        # Cloud Run IAM (roles/run.invoker) is the only gate
    expected = settings.GATEWAY_API_TOKEN
    if not expected or not x_gateway_token or not hmac.compare_digest(expected, x_gateway_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant|model)$")
    content: str


class ChatIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    json_schema: dict | None = Field(default=None, alias="schema")
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=16, le=65536)
    providers: list[str] | None = None        # optional: restrict this call to some providers


@app.get("/health")
@app.get("/healthz")
def health():
    return {"status": "healthy"}


@app.get("/ready")
def ready():
    names = list(router().providers)
    ok = (bool(settings.GATEWAY_API_TOKEN) or not settings.REQUIRE_TOKEN) and bool(names)
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", "providers": names})


@app.get("/v1/providers", dependencies=[Depends(require_caller)])
def providers():
    r = router()
    return {"providers": [{"name": n, "model": p.model, "weight": r._weights.get(n, 0), "cooling_down": r.in_cooldown(n)}
                          for n, p in r.providers.items()]}


@app.post("/v1/chat", dependencies=[Depends(require_caller)])
def chat(data: ChatIn):
    if sum(len(m.content) for m in data.messages) > MAX_CHARS:
        raise HTTPException(status_code=413, detail="Conversation too long")
    started = time.monotonic()
    try:
        result = router().complete(
            [m.model_dump() for m in data.messages], schema=data.json_schema,
            temperature=settings.DEFAULT_TEMPERATURE if data.temperature is None else data.temperature,
            max_tokens=data.max_tokens or settings.DEFAULT_MAX_TOKENS, only=data.providers)
    except NoProviders:
        return JSONResponse(status_code=503, content={"error": "No LLM providers are configured on the gateway.",
                                                      "attempts": [], "retryable": False})
    except AllProvidersFailed as exc:
        return JSONResponse(status_code=503 if exc.retryable else 502,
                            content={"error": "No LLM provider could answer.", "retryable": exc.retryable,
                                     "attempts": [{"provider": a.provider, "kind": a.kind, "message": a.message}
                                                  for a in exc.attempts if not a.ok]})
    c = result.completion
    return {"text": c.text, "provider": result.provider, "model": c.model, "fallback": result.fallback,
            "latency_ms": int((time.monotonic() - started) * 1000), "tokens_in": c.tokens_in, "tokens_out": c.tokens_out}
