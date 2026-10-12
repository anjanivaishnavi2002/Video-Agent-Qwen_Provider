"""Notification-service configuration. Credentials come ONLY from the environment (Secret Manager in production)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"

    # Caller authentication. In production the service is ALSO private on Cloud Run (no allUsers invoker).
    NOTIFICATION_API_TOKEN: str = ""        # shared secret the backend sends in X-Notification-Token
    REQUIRE_TOKEN: bool = True              # set false ONLY when relying purely on Cloud Run IAM (--no-allow-unauthenticated)

    ORGANIZATION_NAME: str = "our team"

    # ---- E-mail ---------------------------------------------------------------------------------
    EMAIL_PROVIDER: str = "console"         # console | resend | smtp | sendgrid
    EMAIL_FROM: str = ""                    # e.g. "Recruiting <recruiting@example.com>"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""                 # secret
    SMTP_USE_TLS: bool = True               # STARTTLS
    SENDGRID_API_KEY: str = ""              # secret
    RESEND_API_KEY: str = ""                # secret (EMAIL_PROVIDER=resend; EMAIL_FROM must be on a domain verified in Resend)
    EMAIL_REPLY_TO: str = ""                # optional

    # ---- SMS ------------------------------------------------------------------------------------
    SMS_PROVIDER: str = "console"           # console | twilio | msg91
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""             # secret
    TWILIO_FROM_NUMBER: str = ""            # or a Messaging Service SID below
    TWILIO_MESSAGING_SERVICE_SID: str = ""
    MSG91_AUTH_KEY: str = ""                # secret (SMS_PROVIDER=msg91)
    MSG91_TEMPLATE_ID: str = ""             # DLT-approved Flow template id (India does not allow free-text SMS)
    MSG91_TEMPLATE_VAR: str = "message"     # the template variable that receives the message text
    MSG91_COUNTRY_CODE: str = "91"          # added to 10-digit numbers

    # ---- Voice calls (text-to-speech phone call) --------------------------------------------------
    VOICE_PROVIDER: str = "console"         # console | twilio (reuses the TWILIO_* credentials above)
    TWILIO_VOICE_FROM_NUMBER: str = ""      # defaults to TWILIO_FROM_NUMBER
    VOICE_LANGUAGE: str = "en-IN"

    REQUEST_TIMEOUT_SECONDS: float = 15.0


settings = Settings()
