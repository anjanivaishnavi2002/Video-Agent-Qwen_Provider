"""
Client for the separate notification-service (its own Cloud Run service).

This module is the ONLY place the backend knows about notifications. It never raises into the interview or admin
flows: a failed e-mail/SMS is recorded in notification_records and the interview carries on.
"""
import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Candidate, Interview, Job, NotificationRecord

logger = logging.getLogger(__name__)

KINDS = ("invitation", "reminder", "completion", "status_update", "custom")
CHANNELS = ("email", "sms", "call")


def interview_link(candidate: Candidate) -> str:
    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}/?invite={candidate.invite_token}"


def _headers(request_id: str) -> dict:
    headers = {"X-Request-Id": request_id}
    mode = settings.NOTIFICATION_AUTH_MODE.lower()
    if mode in ("token", "both") and settings.NOTIFICATION_SERVICE_TOKEN:
        headers["X-Notification-Token"] = settings.NOTIFICATION_SERVICE_TOKEN
    if mode in ("iam", "both"):
        # Cloud Run service-to-service auth: an ID token for the notification service, minted from our service account.
        import google.auth.transport.requests
        import google.oauth2.id_token

        token = google.oauth2.id_token.fetch_id_token(
            google.auth.transport.requests.Request(), settings.NOTIFICATION_SERVICE_URL.rstrip("/"))
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _context(candidate: Candidate, job: Job | None, extra: dict | None) -> dict:
    ctx = {
        "candidate_name": candidate.name,
        "job_title": job.title if job else "",
        "organization_name": settings.ORGANIZATION_NAME,
        "interview_link": interview_link(candidate),
        "status": candidate.interview_status,
    }
    ctx.update(extra or {})
    return ctx


def send_one(db: Session, candidate: Candidate, kind: str, channel: str, *, interview: Interview | None = None,
             admin_id: int | None = None, extra: dict | None = None) -> NotificationRecord:
    recipient = candidate.email if channel == "email" else candidate.phone  # sms and call use the phone
    record = NotificationRecord(candidate_id=candidate.id, interview_id=interview.id if interview else None,
                                channel=channel, kind=kind, recipient=recipient or "-", status="queued",
                                triggered_by=admin_id)
    db.add(record)
    db.commit()
    db.refresh(record)

    def finish(status: str, error: str | None = None, **fields) -> NotificationRecord:
        record.status, record.error = status, (error or None)
        for key, value in fields.items():
            setattr(record, key, value)
        db.commit()
        return record

    if not settings.NOTIFICATIONS_ENABLED or not settings.NOTIFICATION_SERVICE_URL:
        return finish("skipped", "Notifications are not configured (NOTIFICATION_SERVICE_URL).")
    if not recipient:
        return finish("skipped", f"The candidate has no {'e-mail address' if channel == 'email' else 'phone number'}.")

    job = db.get(Job, candidate.job_id) if candidate.job_id else None
    payload = {"channel": channel, "kind": kind, "to": recipient, "context": _context(candidate, job, extra)}
    try:
        response = httpx.post(
            f"{settings.NOTIFICATION_SERVICE_URL.rstrip('/')}/v1/send", json=payload,
            headers=_headers(f"notif-{record.id}"), timeout=settings.NOTIFICATION_TIMEOUT_SECONDS)
        if response.status_code >= 400:
            detail = response.text[:300]
            logger.warning("Notification service returned %s for record %s", response.status_code, record.id)
            return finish("failed", f"HTTP {response.status_code}: {detail}")
        data = response.json()
        return finish("sent" if data.get("status") == "sent" else "failed", data.get("error"),
                      provider=data.get("provider"), provider_message_id=data.get("message_id"),
                      sent_at=datetime.utcnow())
    except Exception as exc:  # network, auth, JSON ... never break the caller
        logger.warning("Notification %s failed: %s", record.id, type(exc).__name__)
        return finish("failed", f"{type(exc).__name__}: {exc}"[:300])


def notify_candidate(db: Session, candidate: Candidate, kind: str, *, channels: list[str] | None = None,
                     interview: Interview | None = None, admin_id: int | None = None,
                     extra: dict | None = None) -> list[NotificationRecord]:
    """Send `kind` over each channel the candidate can be reached on (or the requested channels)."""
    if kind not in KINDS:
        raise ValueError(f"Unknown notification kind '{kind}'")
    wanted = channels or [c for c, value in (("email", candidate.email), ("sms", candidate.phone)) if value]
    return [send_one(db, candidate, kind, channel, interview=interview, admin_id=admin_id, extra=extra)
            for channel in wanted if channel in CHANNELS]
