import logging

from app.config import Settings
from app.clients.twilio import TwilioError
from app.providers.base import ProviderError, SendResult, VoiceProvider
from app.providers.twilio_factory import twilio_client
from app.providers.email import mask

logger = logging.getLogger(__name__)


class ConsoleVoice(VoiceProvider):
    name = "console"

    def call(self, *, to, message) -> SendResult:
        logger.info("[console call] to=%s chars=%d", mask(to), len(message))
        return SendResult(self.name, "console")


class TwilioVoice(VoiceProvider):
    """Twilio Programmable Voice: places the call and reads the message aloud (no webhook needed)."""
    name = "twilio"

    def __init__(self, cfg: Settings):
        self.client = twilio_client(cfg)
        if not self.client.can_call:
            raise ValueError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_VOICE_FROM_NUMBER (or "
                             "TWILIO_FROM_NUMBER) are required for VOICE_PROVIDER=twilio")

    def call(self, *, to, message) -> SendResult:
        try:
            return SendResult(self.name, self.client.place_call(to=to, message=message))
        except TwilioError as exc:
            raise ProviderError(str(exc), retryable=exc.retryable) from exc


def build_voice_provider(cfg: Settings) -> VoiceProvider:
    kind = cfg.VOICE_PROVIDER.strip().lower()
    if kind == "console":
        return ConsoleVoice()
    if kind == "twilio":
        return TwilioVoice(cfg)
    raise ValueError(f"Unknown VOICE_PROVIDER '{cfg.VOICE_PROVIDER}' (console | twilio)")
