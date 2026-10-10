"""
Central configuration for the AI Video Interview backend.

Voice is always Whisper (speech-to-text) on the server -> LLM -> Piper (text-to-speech) on the server.
The LLM is Gemini on Google Cloud Vertex AI (LLM_PROVIDER=gemini, default; Application Default Credentials, no API key).
LLM_PROVIDER=ollama keeps a fully offline self-hosted option for development.
The rest of the app uses settings.active_model.
"""

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _alias(default, *names: str):
    """Allow a setting to be provided through legacy environment names."""
    return Field(
        default=default,
        validation_alias=AliasChoices(*names),
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    # ================================================================
    # DATABASE
    # ================================================================

    DATABASE_URL: str

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        # Render / Heroku hand out "postgres://..." - SQLAlchemy needs "postgresql://..."
        # and our driver is psycopg2 (newer SQLAlchemy would otherwise look for psycopg 3).
        if value.startswith("postgres://"):
            value = "postgresql://" + value[len("postgres://"):]
        if value.startswith("postgresql://"):
            value = "postgresql+psycopg2://" + value[len("postgresql://"):]
        return value

    # ================================================================
    # RUNTIME
    # ================================================================

    ENVIRONMENT: str = "development"        # development | production
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"                # text | json (json = Cloud Logging friendly)

    # ================================================================
    # LLM
    # ================================================================

    # gemini -> Gemini on Google Cloud Vertex AI (Application Default Credentials, no API key)
    # ollama -> self-hosted model through Ollama (offline development only)
    # "vertex" is accepted as an alias of "gemini".
    LLM_PROVIDER: str = "gemini"

    # ---- Vertex AI (LLM_PROVIDER=gemini) -----------------------------------
    # Production: the Cloud Run service account needs roles/aiplatform.user (no key file anywhere).
    # Local dev:  run `gcloud auth application-default login`.
    GOOGLE_CLOUD_PROJECT: str = _alias("", "GOOGLE_CLOUD_PROJECT", "VERTEX_PROJECT_ID")
    GOOGLE_CLOUD_LOCATION: str = _alias("us-central1", "GOOGLE_CLOUD_LOCATION", "VERTEX_LOCATION")
    VERTEX_AI_MODEL: str = _alias("gemini-2.5-flash", "VERTEX_AI_MODEL", "VERTEX_MODEL")
    LLM_FALLBACK_MODEL: str = ""            # optional second model tried when the primary is overloaded
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_THINKING_LEVEL: str = ""            # Gemini 3.x: minimal|low|medium|high ("" = model default)
    LLM_THINKING_BUDGET: int = -1           # Gemini 2.5: token budget (-1 = model default, 0 = off if supported)
    LLM_SEND_SAMPLING_PARAMS: bool = True   # send temperature/top_p (set false for models that deprecate them)

    # ---- Gemini Live (real-time voice interview) ---------------------------
    # live = the candidate's voice streams to Gemini Live and its voice streams back (default).
    # turn = legacy turn-based flow (Whisper -> Gemini text -> Piper); also what scoring/summaries use for text.
    INTERVIEW_MODE: str = "live"
    GEMINI_LIVE_MODEL: str = "gemini-3.8-live"
    GEMINI_LIVE_LOCATION: str = "us-central1"       # Live API is not served from every region; keep separate from the text location
    GEMINI_LIVE_VOICE: str = "Aoede"                # prebuilt voice name
    LIVE_AUTH_TIMEOUT_SECONDS: float = 10.0
    LIVE_GRACE_SECONDS: int = 120                   # after the planned duration, hard stop

    # ---- Self-hosted model (LLM_PROVIDER=ollama) ----------------------------
    ENABLE_OLLAMA: bool = False                     # Qwen/Ollama is DISABLED unless this is true; Gemini is always used otherwise
    OLLAMA_HOST: str = "http://localhost:11434"     # docker compose sets http://ollama:11434
    OLLAMA_MODEL: str = "qwen2.5:3b-instruct"
    OLLAMA_KEEP_ALIVE: str = "30m"                  # keep the model in memory between turns
    OLLAMA_NUM_CTX: int = 4096
    OLLAMA_TIMEOUT_SECONDS: float = 180.0           # CPU: the first reply after a restart is slow

    # Optional explicit override of the model name (OLLAMA_MODEL / VERTEX_AI_MODEL). Normally leave empty.
    LLM_MODEL: str = ""

    # Generation settings.
    LLM_TEMPERATURE: float = 0.7
    LLM_TOP_P: float = 0.9
    LLM_MAX_TOKENS: int = 8192    # on Gemini, thinking tokens count toward this
    LLM_LONG_MAX_TOKENS: int = 16384   # long structured answers (task sets, reports, evaluations)

    # Automatic retry configuration (connection problems / timeouts).
    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_BACKOFF_SECONDS: float = 2.0

    # Resume analysis.
    RESUME_ANALYSIS_TEMPERATURE: float = 0.1

    # Post-interview summary (transcript + observable recording events)
    SUMMARY_AUTO: bool = True                   # generate it automatically once the recording is uploaded
    SUMMARY_MAX_TRANSCRIPT_CHARS: int = 40000
    SUMMARY_TEMPERATURE: float = 0.2

    # ================================================================
    # PROMPTS
    # ================================================================

    PROMPT_FILE: str = "app/prompts/interviewer.yaml"
    BPO_CONTEXT_FILE: str = "app/prompts/bpo_context.yaml"

    # ================================================================
    # INTERVIEW
    # ================================================================

    INTERVIEWER_NAME: str = _alias(
        "Alex",
        "INTERVIEWER_NAME",
        "DEFAULT_INTERVIEWER_NAME",
    )

    # Each interview is hosted by one of these (chosen at random, then fixed for that interview). Comma separated.
    INTERVIEWER_NAMES: str = "Alex,Sam"

    @property
    def interviewer_names(self) -> list[str]:
        names = [n.strip() for n in self.INTERVIEWER_NAMES.split(",") if n.strip()]
        return names or [self.INTERVIEWER_NAME]

    INTERVIEWER_TONE: str = _alias(
        "warm, professional and conversational",
        "INTERVIEWER_TONE",
        "DEFAULT_TONE",
    )

    INTERVIEW_TYPE: str = "BPO / customer operations hiring"

    INTERVIEW_DURATION_MINUTES: int = _alias(
        15,
        "INTERVIEW_DURATION_MINUTES",
        "DEFAULT_INTERVIEW_MINUTES",
    )

    MAX_TURNS: int = _alias(
        25,
        "MAX_TURNS",
        "HARD_TURN_LIMIT",
    )

    MIN_TURNS_BEFORE_END: int = 4

    HISTORY_TURNS_IN_PROMPT: int = 10

    MAX_LEARNED_FACTS: int = 25

    MAX_EMPTY_STREAK: int = 3

    EMPTY_BEFORE_REPROMPT: int = 1

    DEBUG_EXPOSE_TEXT: bool = False

    # ================================================================
    # CONSENT
    # ================================================================

    CONSENT_FILE: str = "app/prompts/consent.yaml"

    ORGANIZATION_NAME: str = "our company"

    DATA_RETENTION_DAYS: int = 90

    CONSENT_CONTACT_EMAIL: str = ""

    # ================================================================
    # RESUME
    # ================================================================

    ALLOWED_RESUME_EXTENSIONS: str = ".pdf,.docx,.txt"

    MAX_RESUME_MB: int = 10

    MIN_RESUME_CHARS: int = 80

    RESUME_STORE_MAX_CHARS: int = 60000

    RESUME_PROMPT_MAX_CHARS: int = 6000

    RESUME_CHUNK_CHARS: int = 5000

    # ================================================================
    # STORAGE
    # ================================================================

    # local -> local filesystem
    # gcs   -> Google Cloud Storage
    STORAGE_BACKEND: str = "local"

    GCS_BUCKET: str = _alias("", "GCS_BUCKET_NAME", "GCS_BUCKET")
    GCS_SIGNING_SERVICE_ACCOUNT: str = ""     # Cloud Run service account email used to sign URLs (IAM signBlob)
    SIGNED_URL_TTL_SECONDS: int = 600

    GCS_PREFIX: str = "video-agent"

    UPLOAD_DIR: str = "uploads"

    RESUME_SUBDIR: str = "resumes"

    VIDEO_SUBDIR: str = "videos"

    ALLOWED_VIDEO_EXTENSIONS: str = ".webm,.mp4"

    MAX_VIDEO_MB: int = 500

    VIDEO_BITS_PER_SECOND: int = 800_000

    # ================================================================
    # SPEECH TO TEXT
    # ================================================================

    WHISPER_MODEL: str = "base"

    WHISPER_DEVICE: str = "cpu"

    WHISPER_COMPUTE_TYPE: str = "int8"

    WHISPER_BEAM_SIZE: int = 1

    WHISPER_VAD: bool = True

    WHISPER_VAD_THRESHOLD: float = 0.3

    WHISPER_VAD_MIN_SILENCE_MS: int = 700

    WHISPER_NO_SPEECH_THRESHOLD: float = 0.85

    WHISPER_MIN_LOGPROB: float = -1.5

    STT_LANGUAGE: str = "en"

    STT_MIN_AUDIO_SECONDS: float = 0.4

    PRELOAD_MODELS: bool = False

    # Proctoring: browser/tab switches that only warn; the next one ends the interview.
    MAX_TAB_SWITCH_WARNINGS: int = 2
    # Chat (written skills) mode
    CHAT_TASK_COUNT: int = 3
    LIVE_EXERCISES: int = 2                  # written exercises (email / chat) the live interviewer may pop up; 0 = off
    CHAT_MAX_CHARS: int = 6000
    CHAT_MAX_MESSAGES: int = 40

    # ================================================================
    # TEXT TO SPEECH
    # ================================================================

    PIPER_VOICE_PATH: str = "voices/en_US-lessac-medium.onnx"

    # ================================================================
    # AUDIO / VAD
    # ================================================================

    VAD_SILENCE_MS: int = 1800

    VAD_MIN_SPEECH_MS: int = 500

    VAD_ONSET_MS: int = 150

    VAD_PREROLL_MS: int = 500

    VAD_MIN_THRESHOLD: float = 0.012

    VAD_NOISE_MULTIPLIER: float = 3.0

    VAD_MAX_NOISE_FLOOR: float = 0.05

    VAD_CALIBRATION_MS: int = 300

    VAD_MAX_UTTERANCE_MS: int = 90_000

    VAD_NO_SPEECH_TIMEOUT_MS: int = 20_000

    AUDIO_TARGET_SAMPLE_RATE: int = 16_000

    # ================================================================
    # FACE MONITORING
    # ================================================================

    FACE_MAX_FACES: int = 2

    FACE_WASM_URL: str = "/mediapipe/wasm"

    FACE_MODEL_URL: str = (
        "https://storage.googleapis.com/"
        "mediapipe-models/face_landmarker/"
        "face_landmarker/float16/1/face_landmarker.task"
    )

    FACE_MISSING_GRACE_MS: int = 2000

    FACE_MOVEMENT_THRESHOLD: float = 0.06

    FACE_MOVEMENT_WINDOW_MS: int = 600

    FACE_EVENT_COOLDOWN_MS: int = 3000

    FACE_EVENT_FLUSH_MS: int = 5000

    ALLOWED_EVENT_TYPES: str = (
        "face_missing,"
        "face_returned,"
        "multiple_faces,"
        "head_movement,"
        "tab_hidden"
    )

    # ================================================================
    # WEB
    # ================================================================

    CORS_ORIGINS: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )

    # ================================================================
    # SECURITY
    # ================================================================

    # Legacy shared key for the session read endpoints. Leave EMPTY in production: use admin login instead.
    ADMIN_API_KEY: str = ""

    ENABLE_DEBUG_ENDPOINTS: bool = False

    # Admin authentication (separate from the candidate flow)
    JWT_SECRET: str = ""                    # >= 32 random chars; Secret Manager in production
    JWT_ALGORITHM: str = "HS256"
    ADMIN_TOKEN_EXPIRE_MINUTES: int = 60
    LOGIN_MAX_ATTEMPTS: int = 5             # per e-mail + client IP, per window
    LOGIN_WINDOW_SECONDS: int = 300
    # Optional first admin, created on startup only when NO admin exists yet (prefer scripts/create_admin.py)
    ADMIN_BOOTSTRAP_EMAIL: str = ""
    ADMIN_BOOTSTRAP_PASSWORD: str = ""

    # Candidate accounts (job portal) and paid recording unlock
    CANDIDATE_TOKEN_EXPIRE_MINUTES: int = 480
    PUBLIC_APPLY_ENABLED: bool = True        # anonymous /resume/upload; set false in production (accounts only)
    UNLOCK_CREDIT_COST: int = 10             # credits taken when an admin unlocks one interview's recording
    REQUIRE_UNLOCK: bool = True              # recording / transcript / written work need an unlock

    # Candidates
    MAX_INTERVIEW_ATTEMPTS: int = 3

    # ================================================================
    # NOTIFICATIONS (separate Cloud Run service)
    # ================================================================

    NOTIFICATIONS_ENABLED: bool = True
    NOTIFICATION_SERVICE_URL: str = ""      # e.g. https://notification-service-xxxx.run.app
    NOTIFICATION_SERVICE_TOKEN: str = ""    # shared bearer secret (Secret Manager)
    NOTIFICATION_AUTH_MODE: str = "token"   # token | iam | both | none  (iam = Google ID token for Cloud Run IAM)
    NOTIFICATION_TIMEOUT_SECONDS: float = 10.0
    FRONTEND_BASE_URL: str = "http://localhost:5173"   # used to build candidate links

    # ================================================================
    # SCHEMA MANAGEMENT
    # ================================================================

    # development/tests: create tables directly. production: run `alembic upgrade head` (Cloud Run Job).
    AUTO_CREATE_SCHEMA: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # ================================================================
    # DATABASE CONNECTION
    # ================================================================

    DB_CONNECT_RETRIES: int = 30

    DB_CONNECT_RETRY_SECONDS: float = 2.0

    # ================================================================
    # HELPERS
    # ================================================================

    @staticmethod
    def _csv(value: str, lower: bool = False) -> list[str]:
        items = [
            item.strip()
            for item in value.split(",")
            if item.strip()
        ]

        if lower:
            return [item.lower() for item in items]

        return items

    @property
    def active_model(self) -> str:
        """LLM_MODEL if set, otherwise the model of the selected provider."""
        if self.llm_backend == "ollama":
            return self.LLM_MODEL.strip() or self.OLLAMA_MODEL.strip()
        return self.LLM_MODEL.strip() or self.VERTEX_AI_MODEL.strip()

    @property
    def llm_backend(self) -> str:
        value = self.LLM_PROVIDER.strip().lower()
        if value == "ollama" and self.ENABLE_OLLAMA:
            return "ollama"
        return "gemini"                              # Qwen/Ollama is disabled by default (ENABLE_OLLAMA=false)

    @property
    def live_mode(self) -> bool:
        return self.INTERVIEW_MODE.strip().lower() == "live"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() == "production"

    @model_validator(mode="after")
    def _production_guards(self):
        if not self.is_production:
            return self
        problems = []
        if len(self.JWT_SECRET) < 32:
            problems.append("JWT_SECRET must be at least 32 characters")
        if any(o.strip() == "*" for o in self.CORS_ORIGINS.split(",")):
            problems.append("CORS_ORIGINS must list the frontend origin(s), not '*'")
        if self.STORAGE_BACKEND.lower() != "gcs":
            problems.append("STORAGE_BACKEND must be 'gcs'")
        if self.ENABLE_DEBUG_ENDPOINTS:
            problems.append("ENABLE_DEBUG_ENDPOINTS must be false")
        if self.AUTO_CREATE_SCHEMA:
            problems.append("AUTO_CREATE_SCHEMA must be false (use Alembic migrations)")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return self._csv(self.CORS_ORIGINS)

    @property
    def allowed_resume_extensions(self) -> set[str]:
        return set(
            self._csv(
                self.ALLOWED_RESUME_EXTENSIONS,
                lower=True,
            )
        )

    @property
    def allowed_video_extensions(self) -> set[str]:
        return set(
            self._csv(
                self.ALLOWED_VIDEO_EXTENSIONS,
                lower=True,
            )
        )

    @property
    def allowed_event_types(self) -> set[str]:
        return set(
            self._csv(
                self.ALLOWED_EVENT_TYPES,
                lower=True,
            )
        )


settings = Settings()