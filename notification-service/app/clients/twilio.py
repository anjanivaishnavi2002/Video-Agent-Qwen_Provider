"""Reusable Twilio client (SMS and text-to-speech phone calls) over the REST API. Needs only httpx.

    client = TwilioClient(account_sid="AC...", auth_token="...", from_number="+1415...")
    sid = client.send_sms(to="+919876543210", body="Your interview link: ...")
    sid = client.place_call(to="+919876543210", message="Hello, this is a reminder ...")
"""
from __future__ import annotations

from xml.sax.saxutils import escape

import httpx

BASE = "https://api.twilio.com/2010-04-01/Accounts"


class TwilioError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class TwilioClient:
    def __init__(self, account_sid: str, auth_token: str, *, from_number: str = "", messaging_service_sid: str = "",
                 voice_from_number: str = "", voice_language: str = "en-US", timeout: float = 15.0,
                 http: httpx.Client | None = None):
        if not (account_sid and auth_token):
            raise ValueError("Twilio needs an account SID and an auth token")
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.messaging_service_sid = messaging_service_sid
        self.voice_from_number = voice_from_number or from_number
        self.voice_language = voice_language
        self.timeout = timeout
        self._http = http

    # ---- capabilities (so callers can validate configuration up front) -------------------------------------
    @property
    def can_sms(self) -> bool:
        return bool(self.from_number or self.messaging_service_sid)

    @property
    def can_call(self) -> bool:
        return bool(self.voice_from_number)

    # ---- public API ----------------------------------------------------------------------------------------
    def send_sms(self, *, to: str, body: str) -> str | None:
        if not self.can_sms:
            raise ValueError("Set a Twilio from number or a messaging service SID to send SMS")
        data = {"To": to, "Body": body}
        if self.messaging_service_sid:
            data["MessagingServiceSid"] = self.messaging_service_sid
        else:
            data["From"] = self.from_number
        return self._post("Messages.json", data, what="message")

    def place_call(self, *, to: str, message: str, repeat: bool = True) -> str | None:
        if not self.can_call:
            raise ValueError("Set a Twilio voice from number to place calls")
        say = f'<Say language="{escape(self.voice_language)}">{escape(message)}</Say>'
        twiml = "<Response>" + say + (f'<Pause length="1"/>{say}' if repeat else "") + "</Response>"
        return self._post("Calls.json", {"To": to, "From": self.voice_from_number, "Twiml": twiml}, what="call")

    # ---- internals -----------------------------------------------------------------------------------------
    def _post(self, resource: str, data: dict, *, what: str) -> str | None:
        url = f"{BASE}/{self.account_sid}/{resource}"
        auth = (self.account_sid, self.auth_token)
        try:
            if self._http is not None:
                response = self._http.post(url, data=data, auth=auth, timeout=self.timeout)
            else:
                response = httpx.post(url, data=data, auth=auth, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise TwilioError(f"Twilio connection problem: {type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("message") or "")[:200]
            except ValueError:
                pass
            raise TwilioError(f"Twilio rejected the {what} ({response.status_code}) {detail}".strip(),
                              retryable=response.status_code >= 500 or response.status_code == 429,
                              status_code=response.status_code)
        try:
            return response.json().get("sid")
        except ValueError:
            return None
