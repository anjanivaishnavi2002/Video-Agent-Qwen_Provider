import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from app.config import Settings
from app.providers.base import EmailProvider, ProviderError, SendResult

logger = logging.getLogger(__name__)


def mask(value: str) -> str:
    """For logs: never print a full address / number."""
    return (value[:2] + "***" + value[-3:]) if len(value) > 6 else "***"


class ConsoleEmail(EmailProvider):
    """Development: logs instead of sending."""
    name = "console"

    def send(self, *, to, subject, text, html=None) -> SendResult:
        logger.info("[console e-mail] to=%s subject=%r", mask(to), subject)
        return SendResult(self.name, "console")


class SmtpEmail(EmailProvider):
    """Any SMTP relay (Gmail Workspace relay, Amazon SES SMTP, SendGrid SMTP, Mailgun ...)."""
    name = "smtp"

    def __init__(self, cfg: Settings):
        if not (cfg.SMTP_HOST and cfg.EMAIL_FROM):
            raise ValueError("SMTP_HOST and EMAIL_FROM are required for EMAIL_PROVIDER=smtp")
        self.cfg = cfg

    def send(self, *, to, subject, text, html=None) -> SendResult:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.cfg.EMAIL_FROM, to, subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP(self.cfg.SMTP_HOST, self.cfg.SMTP_PORT, timeout=self.cfg.REQUEST_TIMEOUT_SECONDS) as smtp:
                if self.cfg.SMTP_USE_TLS:
                    smtp.starttls(context=ssl.create_default_context())
                if self.cfg.SMTP_USERNAME:
                    smtp.login(self.cfg.SMTP_USERNAME, self.cfg.SMTP_PASSWORD)
                smtp.send_message(msg)
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, OSError) as exc:
            raise ProviderError(f"SMTP connection problem: {type(exc).__name__}", retryable=True) from exc
        except smtplib.SMTPException as exc:
            raise ProviderError(f"SMTP error: {type(exc).__name__}") from exc
        return SendResult(self.name, msg.get("Message-ID"))


class SendGridEmail(EmailProvider):
    name = "sendgrid"

    def __init__(self, cfg: Settings):
        if not (cfg.SENDGRID_API_KEY and cfg.EMAIL_FROM):
            raise ValueError("SENDGRID_API_KEY and EMAIL_FROM are required for EMAIL_PROVIDER=sendgrid")
        self.cfg = cfg

    def send(self, *, to, subject, text, html=None) -> SendResult:
        sender = self.cfg.EMAIL_FROM
        from_obj = {"email": sender.split("<")[-1].strip(" >"), **(
            {"name": sender.split("<")[0].strip()} if "<" in sender and sender.split("<")[0].strip() else {})}
        content = [{"type": "text/plain", "value": text}] + ([{"type": "text/html", "value": html}] if html else [])
        body = {"personalizations": [{"to": [{"email": to}]}], "from": from_obj, "subject": subject, "content": content}
        try:
            response = httpx.post("https://api.sendgrid.com/v3/mail/send", json=body,
                                  headers={"Authorization": f"Bearer {self.cfg.SENDGRID_API_KEY}"},
                                  timeout=self.cfg.REQUEST_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ProviderError(f"SendGrid connection problem: {type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 400:
            raise ProviderError(f"SendGrid rejected the message ({response.status_code})",
                                retryable=response.status_code >= 500 or response.status_code == 429)
        return SendResult(self.name, response.headers.get("X-Message-Id"))


def build_email_provider(cfg: Settings) -> EmailProvider:
    kind = cfg.EMAIL_PROVIDER.strip().lower()
    if kind == "console":
        return ConsoleEmail()
    if kind == "smtp":
        return SmtpEmail(cfg)
    if kind == "sendgrid":
        return SendGridEmail(cfg)
    raise ValueError(f"Unknown EMAIL_PROVIDER '{cfg.EMAIL_PROVIDER}' (console | smtp | sendgrid)")
