from fastapi import APIRouter

from app.config import settings
from app.services.consent_service import get_consent

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/public")
def public_config():
    """Non-secret settings the browser needs. Single source of truth for the frontend."""
    return {
        "interviewer_name": settings.interviewer_names[0],
        "interviewer_names": settings.interviewer_names,
        "interview_type": settings.INTERVIEW_TYPE,
        "interview_minutes": settings.INTERVIEW_DURATION_MINUTES,
        "resume": {
            "allowed_extensions": sorted(settings.allowed_resume_extensions),
            "max_mb": settings.MAX_RESUME_MB,
        },
        "voice": {
            "silence_ms": settings.VAD_SILENCE_MS,
            "min_speech_ms": settings.VAD_MIN_SPEECH_MS,
            "onset_ms": settings.VAD_ONSET_MS,
            "preroll_ms": settings.VAD_PREROLL_MS,
            "min_threshold": settings.VAD_MIN_THRESHOLD,
            "noise_multiplier": settings.VAD_NOISE_MULTIPLIER,
            "max_noise_floor": settings.VAD_MAX_NOISE_FLOOR,
            "calibration_ms": settings.VAD_CALIBRATION_MS,
            "max_utterance_ms": settings.VAD_MAX_UTTERANCE_MS,
            "no_speech_timeout_ms": settings.VAD_NO_SPEECH_TIMEOUT_MS,
            "target_sample_rate": settings.AUDIO_TARGET_SAMPLE_RATE,
        },
        "recording": {
            "video_bits_per_second": settings.VIDEO_BITS_PER_SECOND,
            "max_mb": settings.MAX_VIDEO_MB,
        },
        "face": {
            "max_faces": settings.FACE_MAX_FACES,
            "wasm_url": settings.FACE_WASM_URL,
            "model_url": settings.FACE_MODEL_URL,
            "missing_grace_ms": settings.FACE_MISSING_GRACE_MS,
            "movement_threshold": settings.FACE_MOVEMENT_THRESHOLD,
            "movement_window_ms": settings.FACE_MOVEMENT_WINDOW_MS,
            "event_cooldown_ms": settings.FACE_EVENT_COOLDOWN_MS,
            "flush_ms": settings.FACE_EVENT_FLUSH_MS,
        },
    }


@router.get("/consent")
def consent_form():
    """The consent text shown before the interview (rendered with the current settings)."""
    return get_consent()
