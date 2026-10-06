"""
Where uploaded files finally live: local disk (development) or a PRIVATE Google Cloud Storage bucket (production).

The database only stores the reference (gs://bucket/path). Objects are never public: admins reach them through
short-lived V4 signed URLs minted by the backend after an authenticated, authorised request.
"""
import logging
from datetime import timedelta
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_client = None


def is_gcs() -> bool:
    return settings.STORAGE_BACKEND.lower() == "gcs"


def _storage_client():
    global _client
    if _client is None:
        from google.cloud import storage  # imported lazily: only needed for STORAGE_BACKEND=gcs

        _client = storage.Client()  # Application Default Credentials (the Cloud Run service account)
    return _client


def _bucket():
    if not settings.GCS_BUCKET:
        raise RuntimeError("STORAGE_BACKEND=gcs but GCS_BUCKET_NAME is not set")
    return _storage_client().bucket(settings.GCS_BUCKET)


def object_name(key: str) -> str:
    return f"{settings.GCS_PREFIX.strip('/')}/{key.lstrip('/')}".lstrip("/")


def parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://") or "/" not in uri[5:]:
        raise ValueError("not a gs:// URI")
    bucket, name = uri[5:].split("/", 1)
    return bucket, name


def store_file(local_path: str | Path, key: str, content_type: str | None = None) -> str:
    """
    Persist a finished local file and return the location to save in the database.

    local backend -> the same local path.
    gcs backend   -> uploads to gs://<bucket>/<prefix>/<key>, deletes the local copy, returns the gs:// URI.
    """
    local_path = Path(local_path)
    if not is_gcs():
        return str(local_path)

    name = object_name(key)
    _bucket().blob(name).upload_from_filename(str(local_path), content_type=content_type)
    local_path.unlink(missing_ok=True)
    logger.info("Uploaded an object to gs://%s/%s", settings.GCS_BUCKET, name)
    return f"gs://{settings.GCS_BUCKET}/{name}"


def _signing_kwargs() -> dict:
    """Keyless signing on Cloud Run: the service account signs through the IAM Credentials API (signBlob)."""
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    email = settings.GCS_SIGNING_SERVICE_ACCOUNT or getattr(credentials, "service_account_email", None)
    if not email or email == "default":
        raise RuntimeError("Set GCS_SIGNING_SERVICE_ACCOUNT to the service account e-mail used for signed URLs.")
    return {"service_account_email": email, "access_token": credentials.token}


def signed_url(uri: str, *, method: str = "GET", content_type: str | None = None,
               filename: str | None = None, ttl_seconds: int | None = None,
               headers: dict | None = None) -> str:
    """Short-lived V4 signed URL for a gs:// object (download, or upload when method='PUT')."""
    bucket_name, name = parse_gs_uri(uri)
    blob = _storage_client().bucket(bucket_name).blob(name)
    kwargs: dict = {
        "version": "v4", "method": method,
        "expiration": timedelta(seconds=ttl_seconds or settings.SIGNED_URL_TTL_SECONDS),
        **_signing_kwargs(),
    }
    if content_type:
        kwargs["content_type"] = content_type
    if headers:
        kwargs["headers"] = headers
    if filename and method == "GET":
        safe = "".join(c for c in filename if c.isalnum() or c in "._- ") or "download"
        kwargs["response_disposition"] = f'attachment; filename="{safe}"'
    return blob.generate_signed_url(**kwargs)


def upload_target(key: str) -> str:
    """The gs:// URI a client will upload to with a signed PUT URL."""
    return f"gs://{settings.GCS_BUCKET}/{object_name(key)}"


def object_size(uri: str) -> int | None:
    """Size of a stored object in bytes, or None if it does not exist."""
    bucket_name, name = parse_gs_uri(uri)
    blob = _storage_client().bucket(bucket_name).get_blob(name)
    return blob.size if blob else None


def delete_file(uri: str | None) -> None:
    """Best-effort delete of a stored object (gs:// or local path); used for candidate data deletion."""
    if not uri:
        return
    try:
        if uri.startswith("gs://"):
            bucket_name, name = parse_gs_uri(uri)
            _storage_client().bucket(bucket_name).blob(name).delete()
        else:
            Path(uri).unlink(missing_ok=True)
    except Exception:
        logger.warning("Could not delete a stored object", exc_info=True)
