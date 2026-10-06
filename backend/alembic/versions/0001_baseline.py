"""Baseline: the schema as it was before Alembic (candidates, interviews, interview_events).

Creates the tables on a brand-new database; on an existing database it only adds columns that are missing
(the old start-up code used to do the same with ALTER TABLE). No data is touched.
"""
import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import add_column_if_missing, has_table

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("candidates"):
        op.create_table(
            "candidates",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String, nullable=False),
            sa.Column("resume_filename", sa.String),
            sa.Column("resume_path", sa.String),
            sa.Column("resume_text", sa.Text),
            sa.Column("resume_profile", sa.JSON),
            sa.Column("consent_version", sa.String),
            sa.Column("consented_at", sa.DateTime),
            sa.Column("created_at", sa.DateTime),
        )
        op.create_index("ix_candidates_id", "candidates", ["id"])
    else:
        for col in (sa.Column("resume_filename", sa.String), sa.Column("resume_profile", sa.JSON),
                    sa.Column("consent_version", sa.String), sa.Column("consented_at", sa.DateTime)):
            add_column_if_missing("candidates", col)

    if not has_table("interviews"):
        op.create_table(
            "interviews",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("candidate_id", sa.Integer, sa.ForeignKey("candidates.id")),
            sa.Column("status", sa.String),
            sa.Column("access_token", sa.String),
            sa.Column("end_reason", sa.String),
            sa.Column("settings_snapshot", sa.JSON),
            sa.Column("transcript", sa.JSON),
            sa.Column("started_at", sa.DateTime),
            sa.Column("ended_at", sa.DateTime),
            sa.Column("video_path", sa.String),
            sa.Column("video_size_bytes", sa.Integer),
            sa.Column("video_uploaded_at", sa.DateTime),
            sa.Column("summary", sa.JSON),
            sa.Column("created_at", sa.DateTime),
        )
        op.create_index("ix_interviews_id", "interviews", ["id"])
    else:
        for col in (sa.Column("end_reason", sa.String), sa.Column("settings_snapshot", sa.JSON),
                    sa.Column("access_token", sa.String), sa.Column("started_at", sa.DateTime),
                    sa.Column("ended_at", sa.DateTime), sa.Column("video_size_bytes", sa.Integer),
                    sa.Column("video_uploaded_at", sa.DateTime), sa.Column("summary", sa.JSON)):
            add_column_if_missing("interviews", col)

    if not has_table("interview_events"):
        op.create_table(
            "interview_events",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("interview_id", sa.Integer, sa.ForeignKey("interviews.id"), nullable=False),
            sa.Column("event_type", sa.String, nullable=False),
            sa.Column("occurred_at", sa.DateTime, nullable=False),
            sa.Column("offset_ms", sa.Integer),
            sa.Column("details", sa.JSON),
            sa.Column("created_at", sa.DateTime),
        )
        op.create_index("ix_interview_events_id", "interview_events", ["id"])
        op.create_index("ix_interview_events_interview_id", "interview_events", ["interview_id"])


def downgrade() -> None:
    # Deliberately a no-op: the baseline holds production data and must never be dropped by a downgrade.
    pass
