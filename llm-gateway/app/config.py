"""LLM gateway configuration. API keys come ONLY from the environment (Secret Manager in production)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"

    # Caller authentication. In production the service is ALSO private on Cloud Run (no allUsers invoker).
    GATEWAY_API_TOKEN: str = ""             # shared secret the backend sends in X-Gateway-Token
    REQUIRE_TOKEN: bool = True              # false ONLY when relying purely on Cloud Run IAM

    # ---- Providers: a provider is ON when its key is set --------------------------------------------
    OPENAI_API_KEY: str = ""                # secret
    OPENAI_MODEL: str = "gpt-4o-mini"
    ANTHROPIC_API_KEY: str = ""             # secret
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5"
    GEMINI_API_KEY: str = ""                # secret (Gemini API / AI Studio key; Vertex AI is the backend's own path)
    GEMINI_API_MODEL: str = "gemini-2.5-flash"
    DEEPSEEK_API_KEY: str = ""              # secret
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # ---- Routing -----------------------------------------------------------------------------------
    # Requests are SPREAD across the enabled providers in proportion to these weights (0 = never chosen first,
    # still used as a last-resort fallback unless it is also excluded by GATEWAY_EXCLUDE). Format: "openai:2,anthropic:1".
    GATEWAY_WEIGHTS: str = "openai:1,anthropic:1,gemini_api:1,deepseek:1"
    GATEWAY_EXCLUDE: str = ""               # providers never used at all, e.g. "deepseek" (data-residency choice)
    GATEWAY_RETRIES: int = 1                # quick retry on the same provider for transient errors
    GATEWAY_RETRY_BACKOFF_SECONDS: float = 0.5
    GATEWAY_COOLDOWN_SECONDS: float = 60.0  # a failing provider is skipped this long (unless all are failing)
    REQUEST_TIMEOUT_SECONDS: float = 60.0
    DEFAULT_MAX_TOKENS: int = 8192
    DEFAULT_TEMPERATURE: float = 0.7

    @property
    def weights(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for part in self.GATEWAY_WEIGHTS.split(","):
            if ":" in part:
                name, value = part.split(":", 1)
                try:
                    out[name.strip().lower()] = max(0.0, float(value))
                except ValueError:
                    continue
        return out

    @property
    def excluded(self) -> set[str]:
        return {p.strip().lower() for p in self.GATEWAY_EXCLUDE.split(",") if p.strip()}


settings = Settings()
