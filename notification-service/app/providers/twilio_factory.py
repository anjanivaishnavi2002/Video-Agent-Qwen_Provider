from app.clients.twilio import TwilioClient
from app.config import Settings


def twilio_client(cfg: Settings) -> TwilioClient:
    """One place that turns the service settings into a TwilioClient (used by SMS and voice)."""
    if not (cfg.TWILIO_ACCOUNT_SID and cfg.TWILIO_AUTH_TOKEN):
        raise ValueError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are required for the Twilio provider")
    return TwilioClient(cfg.TWILIO_ACCOUNT_SID, cfg.TWILIO_AUTH_TOKEN, from_number=cfg.TWILIO_FROM_NUMBER,
                        messaging_service_sid=cfg.TWILIO_MESSAGING_SERVICE_SID,
                        voice_from_number=cfg.TWILIO_VOICE_FROM_NUMBER, voice_language=cfg.VOICE_LANGUAGE,
                        timeout=cfg.REQUEST_TIMEOUT_SECONDS)
