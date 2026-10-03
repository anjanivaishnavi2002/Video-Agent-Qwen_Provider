"""
Central configuration for the AI Video Interview backend.

LLM architecture
-----------------
LOCAL DEVELOPMENT:
    LLM_PROVIDER=gemini
    Uses Gemini Developer API + GEMINI_API_KEY

GCP PRODUCTION:
    LLM_PROVIDER=vertex
    Uses Vertex AI Gemini + Application Default Credentials
    (Compute Engine service account in production)

The rest of the application uses:
    settings.active_model
"""

from pydantic import AliasChoices, Field, field_validator
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
    # LLM / GEMINI / VERTEX AI
    # ================================================================

    # gemini  -> Gemini Developer API
    # vertex  -> Google Cloud Vertex AI
    LLM_PROVIDER: str = "gemini"

    # Local development only.
    # NEVER commit this value to Git.
    GEMINI_API_KEY: str = ""

    # Model used with Gemini Developer API.
    GEMINI_MODEL: str = "gemini-3.8-flash"

    # ---- Real-time voice (Gemini Live) --------------------------------
    # Native-audio model used by the live interview route, per provider.
    GEMINI_LIVE_MODEL: str = "gemini-3.8-live"
    VERTEX_LIVE_MODEL: str = "gemini-3.8-live"
    LIVE_VOICE: str = ""                      # prebuilt voice name; empty = the model's default voice
    LIVE_SILENCE_MS: int = 1800               # candidate silence that ends their turn (Google-side VAD)
    LIVE_SESSION_RESUMPTION: bool = True      # lets a >15 min interview continue across Google's connection limit
    LIVE_MAX_RECONNECTS: int = 3
    LIVE_WRAPUP_LEAD_SECONDS: int = 90        # tell the interviewer to wrap up this long before the time limit
    LIVE_FORCE_END_GRACE_SECONDS: int = 90    # hard stop this long after the time limit
    LIVE_SILENCE_CHECKIN_SECONDS: int = 20    # candidate silent this long -> interviewer checks in (0 = off)

    # Google Cloud project used by Vertex AI.
    VERTEX_PROJECT_ID: str = _alias(
        "",
        "VERTEX_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
    )

    # Vertex AI location.
    VERTEX_LOCATION: str = "global"

    # Model used with Vertex AI.
    VERTEX_MODEL: str = "gemini-3.8-flash"

    # Optional explicit override.
    #
    # Normally leave this empty.
    # The provider-specific model above will then be selected automatically.
    LLM_MODEL: str = ""

    # Generation settings.
    LLM_TEMPERATURE: float = 0.7
    LLM_TOP_P: float = 0.9

    # Gemini newer models may not need sampling parameters.
    LLM_SEND_SAMPLING_PARAMS: bool = False

    # Thinking level.
    LLM_THINKING_LEVEL: str = "low"

    # Maximum generated output.
    LLM_MAX_TOKENS: int = 1024

    # Network timeout.
    LLM_TIMEOUT_SECONDS: float = 60.0

    # Automatic retry configuration.
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BACKOFF_SECONDS: float = 1.0

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
        "Priya",
        "INTERVIEWER_NAME",
        "DEFAULT_INTERVIEWER_NAME",
    )

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

    GCS_BUCKET: str = ""

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
        "head_movement"
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

    ADMIN_API_KEY: str = ""

    ENABLE_DEBUG_ENDPOINTS: bool = True

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
        """
        Return the model that the currently selected provider should use.

        Priority:

        1. Explicit LLM_MODEL override
        2. VERTEX_MODEL when provider=vertex
        3. GEMINI_MODEL when provider=gemini
        """

        provider = self.LLM_PROVIDER.strip().lower()

        if provider == "vertex":
            default_model = self.VERTEX_MODEL.strip()
        else:
            default_model = self.GEMINI_MODEL.strip()

        override = self.LLM_MODEL.strip()

        if override:
            return override

        return default_model

    @property
    def live_model(self) -> str:
        """Live (native audio) model id for the selected provider."""
        if self.LLM_PROVIDER.strip().lower() == "vertex":
            return self.VERTEX_LIVE_MODEL.strip()
        return self.GEMINI_LIVE_MODEL.strip()

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