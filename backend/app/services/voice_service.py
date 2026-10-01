import io
import logging
import re
import tempfile
import threading
import wave
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Lazy-loaded AI models (+ locks)
# ---------------------------------------------------------
# Locks are needed because FastAPI runs sync endpoints in a thread pool.
# Two requests must not load the same model (or use it) at the same time.

_whisper = None   # faster_whisper.WhisperModel
_voice = None     # piper.PiperVoice

_whisper_lock = threading.Lock()
_voice_lock = threading.Lock()


def _get_whisper():
    """Load Whisper only when STT is actually used."""
    global _whisper

    if _whisper is None:
        from faster_whisper import WhisperModel

        _whisper = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
        )

    return _whisper


def _get_voice():
    """Load the configured Piper voice only when TTS is used."""
    global _voice

    if _voice is None:
        voice_path = Path(settings.PIPER_VOICE_PATH)

        if not voice_path.exists():
            raise FileNotFoundError(
                f"Piper voice model not found: {voice_path}"
            )

        config_path = Path(f"{voice_path}.json")

        if not config_path.exists():
            raise FileNotFoundError(
                f"Piper voice config not found: {config_path}"
            )

        from piper import PiperVoice

        _voice = PiperVoice.load(str(voice_path))

    return _voice


def warm_up() -> None:
    """Load both models now (called at startup if PRELOAD_MODELS=true)."""
    with _whisper_lock:
        _get_whisper()

    with _voice_lock:
        _get_voice()


# ---------------------------------------------------------
# Speech-to-Text
# ---------------------------------------------------------

def transcribe(audio_bytes: bytes, suffix: str = ".webm") -> str:
    """
    Convert candidate audio into text using Faster-Whisper.

    Returns the transcribed text, or "" if nothing was heard.
    """

    if not audio_bytes:
        return ""

    language = (
        None
        if settings.STT_LANGUAGE.lower() == "auto"
        else settings.STT_LANGUAGE
    )

    vad_parameters = (
        {
            "threshold": settings.WHISPER_VAD_THRESHOLD,
            "min_silence_duration_ms": settings.WHISPER_VAD_MIN_SILENCE_MS,
        }
        if settings.WHISPER_VAD
        else None
    )

    temp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix,
            delete=False,
        ) as temp_file:
            temp_file.write(audio_bytes)
            temp_file.flush()
            temp_path = temp_file.name

        # transcribe() returns a lazy generator: the real work happens
        # while we loop over it, so the loop must stay inside the lock.
        with _whisper_lock:
            model = _get_whisper()

            segments, info = model.transcribe(
                temp_path,
                language=language,
                vad_filter=settings.WHISPER_VAD,
                vad_parameters=vad_parameters,
                beam_size=settings.WHISPER_BEAM_SIZE,
                condition_on_previous_text=False,
            )

            if info.duration < settings.STT_MIN_AUDIO_SECONDS:
                return ""

            text_parts: list[str] = []

            for segment in segments:
                text = segment.text.strip()

                if not text:
                    continue

                if segment.no_speech_prob > settings.WHISPER_NO_SPEECH_THRESHOLD:
                    continue

                if segment.avg_logprob < settings.WHISPER_MIN_LOGPROB:
                    continue

                text_parts.append(text)

        logger.info(
            "STT: %.1fs audio, language=%s, segments kept=%d",
            info.duration,
            info.language,
            len(text_parts),
        )

        return " ".join(text_parts).strip()

    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


# ---------------------------------------------------------
# Text-to-Speech
# ---------------------------------------------------------

_MARKDOWN_CHARS = re.compile(r"[*_`#>~|]")
_EMOJI = re.compile(r"[\U00010000-\U0010ffff\u2600-\u27bf]")
_SPACES = re.compile(r"\s+")


def clean_for_tts(text: str) -> str:
    """
    Remove things a voice should not read out loud:
    markdown symbols, emojis, extra new lines.
    """
    text = _MARKDOWN_CHARS.sub("", text)
    text = _EMOJI.sub("", text)
    text = _SPACES.sub(" ", text)
    return text.strip()


def synthesize(text: str) -> bytes:
    """Convert text into a complete WAV file (bytes) using Piper."""

    text = clean_for_tts(text)

    if not text:
        raise ValueError("Text cannot be empty")

    audio_buffer = io.BytesIO()

    with _voice_lock:
        voice = _get_voice()

        with wave.open(audio_buffer, "wb") as wav_file:
            # Piper output: mono, 16-bit PCM, voice-specific sample rate
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(voice.config.sample_rate)

            voice.synthesize_wav(text, wav_file)

    return audio_buffer.getvalue()