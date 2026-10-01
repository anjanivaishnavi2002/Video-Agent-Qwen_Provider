"""Where uploaded files finally live: local disk, or a Google Cloud Storage bucket."""
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_client = None


def _bucket():
    global _client
    if _client is None:
        from google.cloud import storage  # imported lazily: only needed for STORAGE_BACKEND=gcs

        _client = storage.Client()  # uses the VM's service account (Application Default Credentials)
    return _client.bucket(settings.GCS_BUCKET)


def store_file(local_path: str | Path, key: str, content_type: str | None = None) -> str:
    """
    Persist a finished local file and return the location to save in the database.

    local backend -> the same local path.
    gcs backend   -> uploads to gs://<bucket>/<prefix>/<key>, deletes the local copy,
                     and returns the gs:// URI.
    """
    local_path = Path(local_path)

    if settings.STORAGE_BACKEND.lower() != "gcs":
        return str(local_path)

    if not settings.GCS_BUCKET:
        raise RuntimeError("STORAGE_BACKEND=gcs but GCS_BUCKET is not set")

    name = f"{settings.GCS_PREFIX.strip('/')}/{key.lstrip('/')}".lstrip("/")
    _bucket().blob(name).upload_from_filename(str(local_path), content_type=content_type)
    local_path.unlink(missing_ok=True)
    logger.info("Uploaded %s to gs://%s/%s", local_path.name, settings.GCS_BUCKET, name)
    return f"gs://{settings.GCS_BUCKET}/{name}"
