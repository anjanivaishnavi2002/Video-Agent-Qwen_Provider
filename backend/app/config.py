"""
Central configuration.

EVERY tunable value of the backend lives here. Values can be overridden in
`backend/.env` (see `.env.example`). Only DATABASE_URL is mandatory.

Legacy variable names from the earlier architecture (HARD_TURN_LIMIT,
DEFAULT_INTERVIEWER_NAME, ...) are still accepted so an existing .env keeps
working.
"""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _alias(default, *names: str):
    """A setting with a default that can also be set through legacy env names."""
    return Field(default=default, validation_alias=AliasChoices(*names))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str

    # ------------------------------------------------------------------
    # LLM (Qwen through Ollama)
    # ------------------------------------------------------------------
    OLLAMA_HOST: str = "http://127.0.0.1:11434"   # 127.0.0.1, not "localhost": on Windows localhost can resolve to IPv6 where Ollama is not listening
    LLM_MODEL: str = "qwen2.5:3b-instruct"
    LLM_TEMPERATURE: float = 0.7
    LLM_TOP_P: float = 0.9
    LLM_NUM_CTX: int = 8192          # Ollama's default (2048) is far too small for resume + history
    LLM_MAX_TOKENS: int = 400        # cap on one interviewer turn
    LLM_KEEP_ALIVE: str = "30m"      # keep the model in memory between turns
    LLM_TIMEOUT_SECONDS: float = 120.0
    # Resume analysis should be faithful, not creative.
    RESUME_ANALYSIS_TEMPERATURE: float = 0.1

    # ------------------------------------------------------------------
    # Prompts / interview context (data files, not code)
    # ------------------------------------------------------------------
    PROMPT_FILE: str = "app/prompts/interviewer.yaml"
    BPO_CONTEXT_FILE: str = "app/prompts/bpo_context.yaml"

    # ------------------------------------------------------------------
    # Interview behaviour
    # ------------------------------------------------------------------
    INTERVIEWER_NAME: str = _alias("Priya", "INTERVIEWER_NAME", "DEFAULT_INTERVIEWER_NAME")
    INTERVIEWER_TONE: str = _alias("warm, professional and conversational", "INTERVIEWER_TONE", "DEFAULT_TONE")
    INTERVIEW_TYPE: str = "BPO / customer operations hiring"
    INTERVIEW_DURATION_MINUTES: int = _alias(15, "INTERVIEW_DURATION_MINUTES", "DEFAULT_INTERVIEW_MINUTES")
    MAX_TURNS: int = _alias(25, "MAX_TURNS", "HARD_TURN_LIMIT")
    MIN_TURNS_BEFORE_END: int = 4      # model may not end the interview before this many candidate answers
    HISTORY_TURNS_IN_PROMPT: int = 10  # older turns are represented by "topics covered" + "what you learned"
    MAX_LEARNED_FACTS: int = 25        # cap on the running list of facts the interviewer remembers
    MAX_EMPTY_STREAK: int = 3          # consecutive silent/unintelligible turns before the interview is closed
    EMPTY_BEFORE_REPROMPT: int = 1     # empty answers tolerated before the AI asks the candidate to repeat
    # If true, API responses also contain the interviewer/candidate TEXT (debugging only).
    # The candidate UI never renders it.
    DEBUG_EXPOSE_TEXT: bool = False

    # ------------------------------------------------------------------
    # Consent form (text lives in app/prompts/consent.yaml)
    # ------------------------------------------------------------------
    CONSENT_FILE: str = "app/prompts/consent.yaml"
    ORGANIZATION_NAME: str = "our company"     # set to your company name
    DATA_RETENTION_DAYS: int = 90          # wording in the consent form; deletion itself is not automated yet
    CONSENT_CONTACT_EMAIL: str = ""

    # ------------------------------------------------------------------
    # Resume handling
    # ------------------------------------------------------------------
    ALLOWED_RESUME_EXTENSIONS: str = ".pdf,.docx,.txt"
    MAX_RESUME_MB: int = 10
    MIN_RESUME_CHARS: int = 80           # below this the file is probably scanned / empty
    RESUME_STORE_MAX_CHARS: int = 60000      # hard cap on stored text (protects DB + processing)
    # Resume text up to this size is given to the interviewer verbatim.
    # Longer resumes are analysed chunk by chunk and only the merged profile is used.
    RESUME_PROMPT_MAX_CHARS: int = 6000
    RESUME_CHUNK_CHARS: int = 5000

    # ------------------------------------------------------------------
    # Files / recording
    # ------------------------------------------------------------------
    # "local" keeps files under UPLOAD_DIR; "gcs" uploads resumes + recordings to a
    # Google Cloud Storage bucket (credentials come from the VM's service account).
    STORAGE_BACKEND: str = "local"
    GCS_BUCKET: str = ""
    GCS_PREFIX: str = "video-agent"
    UPLOAD_DIR: str = "uploads"
    RESUME_SUBDIR: str = "resumes"
    VIDEO_SUBDIR: str = "videos"
    ALLOWED_VIDEO_EXTENSIONS: str = ".webm,.mp4"
    MAX_VIDEO_MB: int = 500
    VIDEO_BITS_PER_SECOND: int = 800_000   # sent to the browser recorder

    # ------------------------------------------------------------------
    # Speech-to-text (Faster-Whisper)
    # ------------------------------------------------------------------
    WHISPER_MODEL: str = "base"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"
    WHISPER_BEAM_SIZE: int = 1
    WHISPER_VAD: bool = True
    WHISPER_VAD_THRESHOLD: float = 0.3
    WHISPER_VAD_MIN_SILENCE_MS: int = 700
    WHISPER_NO_SPEECH_THRESHOLD: float = 0.85
    WHISPER_MIN_LOGPROB: float = -1.5     # drop segments the model itself is very unsure about
    STT_LANGUAGE: str = "en"              # "auto" = detect
    STT_MIN_AUDIO_SECONDS: float = 0.4    # ignore clips shorter than this
    PRELOAD_MODELS: bool = False

    # ------------------------------------------------------------------
    # Text-to-speech (Piper)
    # ------------------------------------------------------------------
    PIPER_VOICE_PATH: str = "voices/en_US-lessac-medium.onnx"

    # ------------------------------------------------------------------
    # Hands-free voice detection (sent to the browser via /config/public)
    # ------------------------------------------------------------------
    VAD_SILENCE_MS: int = 1800            # silence that ends an answer (1.5 - 2 s recommended)
    VAD_MIN_SPEECH_MS: int = 500          # shorter sounds are treated as noise
    VAD_ONSET_MS: int = 150               # sustained sound needed before speech is declared
    VAD_PREROLL_MS: int = 500             # audio kept from before speech was detected
    VAD_MIN_THRESHOLD: float = 0.012      # absolute RMS floor for "speech" (0..1)
    VAD_NOISE_MULTIPLIER: float = 3.0     # speech must be this many times louder than room noise
    VAD_MAX_NOISE_FLOOR: float = 0.05     # cap so a noisy room can't disable detection
    VAD_CALIBRATION_MS: int = 300         # room-noise sampling window each time listening starts
    VAD_MAX_UTTERANCE_MS: int = 90_000    # safety cut-off for one answer
    VAD_NO_SPEECH_TIMEOUT_MS: int = 20_000  # candidate silent this long -> AI checks in (0 = off)
    AUDIO_TARGET_SAMPLE_RATE: int = 16_000  # sample rate of the WAV sent to Whisper

    # ------------------------------------------------------------------
    # Face monitoring (observable events only; sent to the browser)
    # ------------------------------------------------------------------
    FACE_MAX_FACES: int = 2
    # Engine files are served by the frontend itself (copied from node_modules, so the
    # version always matches). The model is fetched from Google unless you download it
    # to frontend/public/mediapipe/face_landmarker.task and set FACE_MODEL_URL to
    # /mediapipe/face_landmarker.task
    FACE_WASM_URL: str = "/mediapipe/wasm"
    FACE_MODEL_URL: str = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
    FACE_MISSING_GRACE_MS: int = 2000     # face must be absent this long to count as missing
    FACE_MOVEMENT_THRESHOLD: float = 0.06  # normalised displacement inside the window
    FACE_MOVEMENT_WINDOW_MS: int = 600
    FACE_EVENT_COOLDOWN_MS: int = 3000    # min gap between two events of the same type
    FACE_EVENT_FLUSH_MS: int = 5000       # how often the browser batches events to the API
    ALLOWED_EVENT_TYPES: str = "face_missing,face_returned,multiple_faces,head_movement"

    # ------------------------------------------------------------------
    # Web
    # ------------------------------------------------------------------
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ------------------------------------------------------------------
    # Security / deployment
    # ------------------------------------------------------------------
    # When set, recruiters must send this value in the X-API-Key header to read results.
    ADMIN_API_KEY: str = ""
    # Raw /voice/stt + /voice/tts endpoints are for local debugging; switch off in production.
    ENABLE_DEBUG_ENDPOINTS: bool = True
    DB_CONNECT_RETRIES: int = 30          # wait for the database (Cloud SQL proxy) to come up
    DB_CONNECT_RETRY_SECONDS: float = 2.0

    # ---- helpers ------------------------------------------------------
    @staticmethod
    def _csv(value: str, lower: bool = False) -> list[str]:
        items = [i.strip() for i in value.split(",") if i.strip()]
        return [i.lower() for i in items] if lower else items

    @property
    def cors_origins_list(self) -> list[str]:
        return self._csv(self.CORS_ORIGINS)

    @property
    def allowed_resume_extensions(self) -> set[str]:
        return set(self._csv(self.ALLOWED_RESUME_EXTENSIONS, lower=True))

    @property
    def allowed_video_extensions(self) -> set[str]:
        return set(self._csv(self.ALLOWED_VIDEO_EXTENSIONS, lower=True))

    @property
    def allowed_event_types(self) -> set[str]:
        return set(self._csv(self.ALLOWED_EVENT_TYPES, lower=True))


settings = Settings()
