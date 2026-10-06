"""Message templates. The backend sends only {kind, channel, to, context}; wording lives here."""
from html import escape

KINDS = ("invitation", "reminder", "completion", "status_update", "custom")

_STATUS_TEXT = {
    "shortlisted": "You have been shortlisted for the next step.",
    "rejected": "We will not be moving forward with your application at this time.",
    "on_hold": "Your application is on hold for now.",
    "invited": "You have been invited to an interview.",
    "completed": "Your interview has been received.",
}


def _ctx(context: dict) -> dict:
    return {
        "name": str(context.get("candidate_name") or "there").strip()[:120],
        "job": str(context.get("job_title") or "").strip()[:200],
        "org": str(context.get("organization_name") or "our team").strip()[:120],
        "link": str(context.get("interview_link") or "").strip()[:500],
        "status": str(context.get("status") or "").strip(),
        "message": str(context.get("message") or "").strip()[:1000],
        "subject": str(context.get("subject") or "").strip()[:200],
    }


def render_email(kind: str, context: dict) -> tuple[str, str, str]:
    """Returns (subject, text, html)."""
    c = _ctx(context)
    role = f" for the {c['job']} position" if c["job"] else ""
    if kind == "invitation":
        subject = f"Your interview invitation - {c['org']}"
        lines = [f"Hi {c['name']},", f"You are invited to complete an AI-led video interview{role} with {c['org']}.",
                 "It takes about 15 minutes. You will need a quiet place, a camera and a microphone.",
                 f"Start here: {c['link']}", "This link is personal to you - please do not share it."]
    elif kind == "reminder":
        subject = f"Reminder: your interview with {c['org']}"
        lines = [f"Hi {c['name']},", f"This is a friendly reminder to complete your video interview{role}.",
                 f"Start here: {c['link']}"]
    elif kind == "completion":
        subject = f"We received your interview - {c['org']}"
        lines = [f"Hi {c['name']},", f"Thank you for completing your interview{role}.",
                 "Our team will review it and get back to you."]
    elif kind == "status_update":
        subject = f"An update on your application - {c['org']}"
        lines = [f"Hi {c['name']},", _STATUS_TEXT.get(c["status"], "There is an update on your application."),
                 f"Regards, {c['org']}"]
    elif kind == "custom":
        if not c["message"]:
            raise ValueError("A custom message needs text")
        subject = c["subject"] or f"A message from {c['org']}"
        lines = [f"Hi {c['name']},", *[p for p in c["message"].split("\n") if p.strip()], f"Regards, {c['org']}"]
    else:
        raise ValueError(f"Unknown kind '{kind}'")
    text = "\n\n".join(lines)
    html = "".join(f"<p>{escape(line)}</p>" if "http" not in line else
                   f'<p><a href="{escape(c["link"])}">Start your interview</a></p>' for line in lines)
    return subject, text, html


def render_sms(kind: str, context: dict) -> str:
    c = _ctx(context)
    if kind == "invitation":
        return f"{c['org']}: Hi {c['name']}, your video interview is ready. Start: {c['link']}"
    if kind == "reminder":
        return f"{c['org']}: Reminder, {c['name']} - please complete your video interview: {c['link']}"
    if kind == "completion":
        return f"{c['org']}: Thanks {c['name']}, we received your interview."
    if kind == "status_update":
        return f"{c['org']}: {_STATUS_TEXT.get(c['status'], 'There is an update on your application.')}"
    if kind == "custom":
        if not c["message"]:
            raise ValueError("A custom message needs text")
        return f"{c['org']}: {c['message']}"[:480]
    raise ValueError(f"Unknown kind '{kind}'")


def render_voice(kind: str, context: dict) -> str:
    """Spoken script for a phone call (no links - they cannot be read aloud; the SMS/e-mail carries those)."""
    c = _ctx(context)
    role = f" for the {c['job']} position" if c["job"] else ""
    hello = f"Hello {c['name']}, this is {c['org']}."
    if kind == "custom":
        if not c["message"]:
            raise ValueError("A custom message needs text")
        return f"{hello} {c['message']}"
    if kind in ("invitation", "reminder"):
        return (f"{hello} We are calling about your video interview{role}. We have sent you a personal link "
                "by e-mail or text message. Please open it to start your interview. Thank you.")
    if kind == "completion":
        return f"{hello} Thank you for completing your interview{role}. Our team will review it and get back to you."
    if kind == "status_update":
        return f"{hello} {_STATUS_TEXT.get(c['status'], 'There is an update on your application.')} Thank you."
    raise ValueError(f"Unknown kind '{kind}'")
