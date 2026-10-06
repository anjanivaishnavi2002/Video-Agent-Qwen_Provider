import logging

import httpx

from app.config import Settings
from app.providers.base import ProviderError, SendResult, SmsProvider
from app.providers.email import mask

logger = logging.getLogger(__name__)


class ConsoleSms(SmsProvider):
    name = "console"

    def send(self, *, to, body) -> SendResult:
        logger.info("[console sms] to=%s chars=%d", mask(to), len(body))
        return SendResult(self.name, "console")


class TwilioSms(SmsProvider):
    """Twilio Programmable Messaging through its REST API (no SDK needed)."""
    name = "twilio"

    def __init__(self, cfg: Settings):
        if not (cfg.TWILIO_ACCOUNT_SID and cfg.TWILIO_AUTH_TOKEN
                and (cfg.TWILIO_FROM_NUMBER or cfg.TWILIO_MESSAGING_SERVICE_SID)):
            raise ValueError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER (or "
                             "TWILIO_MESSAGING_SERVICE_SID) are required for SMS_PROVIDER=twilio")
        self.cfg = cfg

    def send(self, *, to, body) -> SendResult:
        data = {"To": to, "Body": body}
        if self.cfg.TWILIO_MESSAGING_SERVICE_SID:
            data["MessagingServiceSid"] = self.cfg.TWILIO_MESSAGING_SERVICE_SID
        else:
            data["From"] = self.cfg.TWILIO_FROM_NUMBER
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.cfg.TWILIO_ACCOUNT_SID}/Messages.json"
        try:
            response = httpx.post(url, data=data, auth=(self.cfg.TWILIO_ACCOUNT_SID, self.cfg.TWILIO_AUTH_TOKEN),
                                  timeout=self.cfg.REQUEST_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Twilio connection problem: {type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 400:
            raise ProviderError(f"Twilio rejected the message ({response.status_code})",
                                retryable=response.status_code >= 500 or response.status_code == 429)
        return SendResult(self.name, response.json().get("sid"))


def build_sms_provider(cfg: Settings) -> SmsProvider:
    kind = cfg.SMS_PROVIDER.strip().lower()
    if kind == "console":
        return ConsoleSms()
    if kind == "twilio":
        return TwilioSms(cfg)
    raise ValueError(f"Unknown SMS_PROVIDER '{cfg.SMS_PROVIDER}' (console | twilio)")
