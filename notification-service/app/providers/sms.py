import logging

from app.config import Settings
from app.clients.msg91 import Msg91Client, Msg91Error
from app.clients.twilio import TwilioError
from app.providers.base import ProviderError, SendResult, SmsProvider
from app.providers.twilio_factory import twilio_client
from app.providers.email import mask

logger = logging.getLogger(__name__)


class ConsoleSms(SmsProvider):
    name = "console"

    def send(self, *, to, body) -> SendResult:
        logger.info("[console sms] to=%s chars=%d", mask(to), len(body))
        return SendResult(self.name, "console")


class TwilioSms(SmsProvider):
    """Twilio Programmable Messaging. Thin adapter over the reusable TwilioClient."""
    name = "twilio"

    def __init__(self, cfg: Settings):
        self.client = twilio_client(cfg)
        if not self.client.can_sms:
            raise ValueError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER (or "
                             "TWILIO_MESSAGING_SERVICE_SID) are required for SMS_PROVIDER=twilio")

    def send(self, *, to, body) -> SendResult:
        try:
            return SendResult(self.name, self.client.send_sms(to=to, body=body))
        except TwilioError as exc:
            raise ProviderError(str(exc), retryable=exc.retryable) from exc


class Msg91Sms(SmsProvider):
    """MSG91 Flow API (DLT templates, for India). The message text fills one variable of the approved template.

    A missing key or template id does NOT stop the service starting (that would block e-mail and calls too); each SMS
    attempt then fails with a clear message that shows in the admin Notifications tab."""
    name = "msg91"

    def __init__(self, cfg: Settings):
        self.var = cfg.MSG91_TEMPLATE_VAR
        self.client = None
        self.config_error = None
        try:
            self.client = Msg91Client(cfg.MSG91_AUTH_KEY, cfg.MSG91_TEMPLATE_ID,
                                      default_country_code=cfg.MSG91_COUNTRY_CODE, timeout=cfg.REQUEST_TIMEOUT_SECONDS)
        except ValueError as exc:
            self.config_error = f"MSG91 is not configured: {exc}"
            logger.warning(self.config_error)

    def send(self, *, to, body) -> SendResult:
        if self.client is None:
            raise ProviderError(self.config_error or "MSG91 is not configured")
        try:
            return SendResult(self.name, self.client.send_flow(to=to, variables={self.var: body}))
        except Msg91Error as exc:
            raise ProviderError(str(exc), retryable=exc.retryable) from exc
        except ValueError as exc:
            raise ProviderError(str(exc)) from exc


def build_sms_provider(cfg: Settings) -> SmsProvider:
    kind = cfg.SMS_PROVIDER.strip().lower()
    if kind == "console":
        return ConsoleSms()
    if kind == "twilio":
        return TwilioSms(cfg)
    if kind == "msg91":
        return Msg91Sms(cfg)
    raise ValueError(f"Unknown SMS_PROVIDER '{cfg.SMS_PROVIDER}' (console | twilio | msg91)")
