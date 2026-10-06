import logging
from xml.sax.saxutils import escape

import httpx

from app.config import Settings
from app.providers.base import ProviderError, SendResult, VoiceProvider
from app.providers.email import mask

logger = logging.getLogger(__name__)


class ConsoleVoice(VoiceProvider):
    name = "console"

    def call(self, *, to, message) -> SendResult:
        logger.info("[console call] to=%s chars=%d", mask(to), len(message))
        return SendResult(self.name, "console")


class TwilioVoice(VoiceProvider):
    """Twilio Programmable Voice: places the call and reads the message with text-to-speech (no webhook needed)."""
    name = "twilio"

    def __init__(self, cfg: Settings):
        self.from_number = cfg.TWILIO_VOICE_FROM_NUMBER or cfg.TWILIO_FROM_NUMBER
        if not (cfg.TWILIO_ACCOUNT_SID and cfg.TWILIO_AUTH_TOKEN and self.from_number):
            raise ValueError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_VOICE_FROM_NUMBER (or "
                             "TWILIO_FROM_NUMBER) are required for VOICE_PROVIDER=twilio")
        self.cfg = cfg

    def call(self, *, to, message) -> SendResult:
        twiml = (f'<Response><Say language="{escape(self.cfg.VOICE_LANGUAGE)}">{escape(message)}</Say>'
                 f'<Pause length="1"/><Say language="{escape(self.cfg.VOICE_LANGUAGE)}">{escape(message)}</Say>'
                 "</Response>")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.cfg.TWILIO_ACCOUNT_SID}/Calls.json"
        try:
            response = httpx.post(url, data={"To": to, "From": self.from_number, "Twiml": twiml},
                                  auth=(self.cfg.TWILIO_ACCOUNT_SID, self.cfg.TWILIO_AUTH_TOKEN),
                                  timeout=self.cfg.REQUEST_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Twilio connection problem: {type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 400:
            raise ProviderError(f"Twilio rejected the call ({response.status_code})",
                                retryable=response.status_code >= 500 or response.status_code == 429)
        return SendResult(self.name, response.json().get("sid"))


def build_voice_provider(cfg: Settings) -> VoiceProvider:
    kind = cfg.VOICE_PROVIDER.strip().lower()
    if kind == "console":
        return ConsoleVoice()
    if kind == "twilio":
        return TwilioVoice(cfg)
    raise ValueError(f"Unknown VOICE_PROVIDER '{cfg.VOICE_PROVIDER}' (console | twilio)")
