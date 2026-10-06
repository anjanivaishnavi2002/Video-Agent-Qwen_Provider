"""Logging. JSON on Cloud Run (Cloud Logging parses `severity` and `message`), readable text locally."""
import json
import logging
import sys

_SEVERITY = {"WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL", "INFO": "INFO", "DEBUG": "DEBUG"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"severity": _SEVERITY.get(record.levelname, "DEFAULT"), "message": record.getMessage(),
                   "logger": record.name}
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", fmt: str = "text") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Never log request bodies / headers; keep HTTP client libraries quiet (their URLs can contain tokens).
    for noisy in ("httpx", "httpcore", "urllib3", "google.auth", "google_auth_httplib2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
