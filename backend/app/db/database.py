import logging
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

def _make_engine():
    kwargs: dict = {"pool_pre_ping": True}
    if not settings.DATABASE_URL.startswith("sqlite"):
        # Cloud SQL has a connection cap: keep the per-instance pool small (instances x pool_size must fit).
        kwargs.update(pool_size=settings.DB_POOL_SIZE, max_overflow=settings.DB_MAX_OVERFLOW,
                      pool_recycle=settings.DB_POOL_RECYCLE_SECONDS)
    return create_engine(settings.DATABASE_URL, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    Wait for the database. Tables are created here ONLY when AUTO_CREATE_SCHEMA=true (local dev / tests).
    Production schema changes are applied with `alembic upgrade head` (see docs/DEPLOYMENT.md).
    """
    _wait_for_database()
    if settings.AUTO_CREATE_SCHEMA:
        from app.db import models  # noqa: F401  (registers the tables)

        Base.metadata.create_all(bind=engine)
        _add_missing_columns()


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


def _add_missing_columns() -> None:
    """
    Local development only (AUTO_CREATE_SCHEMA=true): `create_all` never changes a table that already exists, so a
    database created by an older version lacks the newest columns. Add them (nullable / with a default) - additive,
    never drops or rewrites data. Production uses Alembic instead.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                default = ""
                if column.default is not None and getattr(column.default, "is_scalar", False):
                    value = column.default.arg
                    default = f" DEFAULT {int(value) if isinstance(value, bool) else repr(value)}"
                null = "" if column.nullable or default else ""
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}{default}{null}'))
                logger.warning("Added missing column %s.%s (development schema sync)", table.name, column.name)
