"""Alembic migrations on a REAL PostgreSQL database (skipped unless MIGRATION_TEST_DATABASE_URL is set).

    MIGRATION_TEST_DATABASE_URL=postgresql+psycopg2://user:pw@localhost:5432/scratch python -m pytest tests/test_migrations.py

The database must be empty / disposable: the test creates a legacy-shaped schema with data, upgrades it, and checks
that nothing was lost and that the models and migrations agree.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

URL = os.environ.get("MIGRATION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MIGRATION_TEST_DATABASE_URL not set")
BACKEND = Path(__file__).resolve().parents[1]


def alembic(*args):
    env = {**os.environ, "DATABASE_URL": URL, "AUTO_CREATE_SCHEMA": "false"}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True)


def test_legacy_database_is_upgraded_without_losing_data():
    engine = sa.create_engine(URL)
    with engine.begin() as c:
        c.execute(sa.text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    assert alembic("upgrade", "0001").returncode == 0                      # the pre-Alembic schema
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO candidates(name, resume_text, created_at) VALUES ('Old', 'x', now())"))
        c.execute(sa.text("INSERT INTO interviews(candidate_id, status, transcript, created_at) VALUES "
                          "(1, 'finished', '[{\"role\":\"assistant\",\"text\":\"Hi\"}]', now())"))
    result = alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    with engine.connect() as c:
        row = c.execute(sa.text("SELECT name, interview_status, interview_attempts, invite_token FROM candidates")).one()
        assert row.name == "Old" and row.interview_status == "completed" and row.interview_attempts == 1 and row.invite_token
        assert c.execute(sa.text("SELECT count(*) FROM interview_turns")).scalar() == 1
    assert alembic("upgrade", "head").returncode == 0                      # idempotent
    check = alembic("check")
    assert check.returncode == 0, check.stdout + check.stderr             # models == migrations
