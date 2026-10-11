"""Reusable Resend e-mail client. No dependency on this service: copy the file into any project (needs only httpx).

    client = ResendClient(api_key="re_...", from_email="Recruiting <hr@yourdomain.com>")
    message_id = client.send(to="a@b.com", subject="Hi", text="Hello", html="<p>Hello</p>")
"""
from __future__ import annotations

import httpx

API_URL = "https://api.resend.com/emails"


class ResendError(Exception):
    """Resend refused or could not be reached. `retryable` says whether trying again can help."""

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class ResendClient:
    def __init__(self, api_key: str, from_email: str, *, timeout: float = 15.0, http: httpx.Client | None = None):
        if not api_key or not from_email:
            raise ValueError("Resend needs an API key and a from address")
        self.api_key = api_key
        self.from_email = from_email
        self.timeout = timeout
        self._http = http          # injectable (tests); a fresh short-lived client is used otherwise

    def send(self, *, to: str | list[str], subject: str, text: str | None = None, html: str | None = None,
             reply_to: str | None = None, tags: dict[str, str] | None = None,
             idempotency_key: str | None = None) -> str | None:
        """Send one e-mail and return Resend's message id."""
        if not (text or html):
            raise ValueError("An e-mail needs a text or html body")
        body: dict = {"from": self.from_email, "to": [to] if isinstance(to, str) else list(to), "subject": subject}
        if text:
            body["text"] = text
        if html:
            body["html"] = html
        if reply_to:
            body["reply_to"] = reply_to
        if tags:
            body["tags"] = [{"name": k, "value": v} for k, v in tags.items()]
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            if self._http is not None:
                response = self._http.post(API_URL, json=body, headers=headers, timeout=self.timeout)
            else:
                response = httpx.post(API_URL, json=body, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ResendError(f"Resend connection problem: {type(exc).__name__}", retryable=True) from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("message") or "")[:200]
            except ValueError:
                pass
            raise ResendError(f"Resend rejected the message ({response.status_code}) {detail}".strip(),
                              retryable=response.status_code >= 500 or response.status_code == 429,
                              status_code=response.status_code)
        try:
            return response.json().get("id")
        except ValueError:
            return None
