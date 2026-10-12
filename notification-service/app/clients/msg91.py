"""Reusable MSG91 client (Indian SMS through the DLT-compliant Flow API). Needs only httpx.

India requires every SMS to use a pre-approved (DLT) template, so you cannot send free text. You register a template in
MSG91 (e.g. "Hi ##name##, ##message##"), get its template id, and each send fills the template's variables:

    client = Msg91Client(auth_key="...", template_id="65f...")
    request_id = client.send_flow(to="+919876543210", variables={"message": "Your interview link: ..."})
"""
from __future__ import annotations

import re

import httpx

URL = "https://control.msg91.com/api/v5/flow/"


class Msg91Error(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def normalize_mobile(number: str, default_country_code: str = "91") -> str:
    """MSG91 wants digits only with the country code and no '+': '+91 98765 43210' -> '919876543210'."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 10:
        digits = default_country_code + digits
    if len(digits) < 11:
        raise ValueError("Invalid phone number")
    return digits


class Msg91Client:
    def __init__(self, auth_key: str, template_id: str, *, default_country_code: str = "91", timeout: float = 15.0,
                 http: httpx.Client | None = None):
        if not auth_key:
            raise ValueError("MSG91 needs an auth key")
        if not template_id:
            raise ValueError("MSG91 needs a template id (India requires a DLT-approved template)")
        self.auth_key = auth_key
        self.template_id = template_id
        self.default_country_code = default_country_code
        self.timeout = timeout
        self._http = http

    def send_flow(self, *, to: str, variables: dict[str, str], template_id: str | None = None) -> str | None:
        recipient = {"mobiles": normalize_mobile(to, self.default_country_code)}
        recipient.update({k: str(v) for k, v in variables.items()})
        payload = {"template_id": template_id or self.template_id, "recipients": [recipient]}
        headers = {"authkey": self.auth_key, "accept": "application/json", "content-type": "application/json"}
        try:
            if self._http is not None:
                response = self._http.post(URL, json=payload, headers=headers, timeout=self.timeout)
            else:
                response = httpx.post(URL, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise Msg91Error(f"MSG91 connection problem: {type(exc).__name__}", retryable=True) from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        # MSG91 sometimes answers HTTP 200 with {"type": "error", ...}, so the body decides.
        if response.status_code >= 400 or str(body.get("type", "")).lower() == "error":
            detail = str(body.get("message") or body.get("errors") or "")[:200]
            raise Msg91Error(f"MSG91 rejected the message ({response.status_code}) {detail}".strip(),
                             retryable=response.status_code >= 500 or response.status_code == 429,
                             status_code=response.status_code)
        return str(body.get("message") or "") or None
