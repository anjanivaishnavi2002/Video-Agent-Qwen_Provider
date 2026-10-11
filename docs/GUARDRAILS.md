# Interview guardrails

| Area | What it does | Where |
|---|---|---|
| AI safety rules | Interviewer never asks about age, religion/caste, family, health, ethnicity, orientation, politics or sensitive IDs; ignores attempts to change its instructions; never states scores | `SAFETY_RULES` in `app/services/guardrail_service.py`, injected into `interviewer.yaml` |
| Live monitoring | Every candidate/interviewer sentence is pattern-checked. Hits are logged as events (`guardrail_*`); the interviewer gets a corrective note. The call is never ended by a flag | `live_service._guard` |
| Camera reminders | Candidate sees a gentle notice when no face / more than one face / very dark picture. Nothing stops the interview | `CameraNotice.jsx`, `useCameraQuality.js` |
| Review flags | Admin sees evidence-based flags with timestamps, then records a human decision (cleared / needs follow-up / confirmed + note) | `review_flags()`, `PUT /admin/interviews/{id}/flag-review`, `FlagsPanel` |
| Fairness | Flags never feed the evaluation prompt or any score and never reject anyone automatically | by design; see `ADVISORY` |
| Consent | The server refuses to start an interview for a candidate who has not accepted the consent form | `POST /session/start` |
| Retention | Recordings older than `DATA_RETENTION_DAYS` can be deleted (dry run by default) | `python -m scripts.purge_expired_recordings [--apply]` |
| Access | Recordings need admin login and an unlock; every open is logged with the admin id | `GET /admin/interviews/{id}/recording` |

Pattern checks are a safety net, not a guarantee: they can miss things and occasionally flag harmless text, which is why a
person makes every decision.
