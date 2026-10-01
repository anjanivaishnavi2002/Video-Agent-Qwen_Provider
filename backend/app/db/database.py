import logging
import time

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the first version of the schema. `create_all` only creates
# missing TABLES, so existing databases get these added here (idempotent).
# The same statements are available as backend/migrations/001_dynamic_interview.sql
_ADDED_COLUMNS = {
    "candidates": {
        "resume_filename": "VARCHAR",
        "resume_profile": "JSON",
        "consent_version": "VARCHAR",
        "consented_at": "TIMESTAMP",
    },
    "interviews": {
        "end_reason": "VARCHAR",
        "settings_snapshot": "JSON",
        "access_token": "VARCHAR",
        "started_at": "TIMESTAMP",
        "ended_at": "TIMESTAMP",
        "video_size_bytes": "INTEGER",
        "video_uploaded_at": "TIMESTAMP",
    },
}


def init_db() -> None:
    """Create missing tables and add missing columns to existing ones."""
    from app.db import models  # noqa: F401  (registers the tables)

    _wait_for_database()
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    postgres = engine.dialect.name == "postgresql"
    for table, columns in _ADDED_COLUMNS.items():
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, sql_type in columns.items():
            if name in existing:
                continue
            # IF NOT EXISTS makes this safe when two processes start at the same moment
            # (uvicorn --reload, several containers). Each column gets its own transaction.
            clause = "IF NOT EXISTS " if postgres else ""
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {clause}{name} {sql_type}")
                    )
            except Exception as exc:
                if "already exists" in str(exc).lower() or "duplicate column" in str(exc).lower():
                    continue  # another process added it first
                raise


def _wait_for_database() -> None:
    """In containers the database may start after the backend; retry instead of crashing."""
    for attempt in range(1, settings.DB_CONNECT_RETRIES + 1):
        try:
            with engine.connect():
                return
        except Exception as exc:
            if attempt == settings.DB_CONNECT_RETRIES:
                raise
            logger.warning("Database not ready (%s). Retry %d/%d", exc.__class__.__name__, attempt, settings.DB_CONNECT_RETRIES)
            time.sleep(settings.DB_CONNECT_RETRY_SECONDS)
