"""Shared helpers for the admin API."""
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Query

from app.services import storage


def paginate(query: Query, page: int, page_size: int) -> tuple[list[Any], int]:
    total = query.order_by(None).count()
    items = query.limit(page_size).offset((page - 1) * page_size).all()
    return items, total


def like_pattern(term: str) -> str:
    """Escape LIKE wildcards in user input."""
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def file_response(uri: str | None, *, filename: str, media_type: str | None = None, label: str = "File"):
    """
    Secure access to a stored object: a short-lived signed URL for Cloud Storage objects (the bucket stays private),
    or the file itself for the local development backend.
    """
    if not uri:
        raise HTTPException(status_code=404, detail=f"{label} not available")
    if uri.startswith("gs://"):
        try:
            url = storage.signed_url(uri, filename=filename)
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Could not create a download link.") from exc
        from app.config import settings
        return {"url": url, "expires_in_seconds": settings.SIGNED_URL_TTL_SECONDS, "filename": filename}
    from pathlib import Path

    path = Path(uri)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return FileResponse(str(path), filename=filename, media_type=media_type)
