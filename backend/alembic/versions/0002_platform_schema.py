"""Platform schema: admin users, jobs, evaluations, interview turns, notification records, and the new
candidate / interview columns + indexes. Additive and idempotent: existing rows are kept and back-filled.
"""
import secrets

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import add_column_if_missing, create_index_if_missing, has_table

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # ---- new tables -------------------------------------------------------------------------
    if not has_table("admin_users"):
        op.create_table(
            "admin_users",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("full_name", sa.String(200)),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("role", sa.String(30), nullable=False, server_default="recruiter"),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("token_version", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_login_at", sa.DateTime),
            sa.Column("created_at", sa.DateTime),
            sa.Column("updated_at", sa.DateTime),
        )
        op.create_index("ix_admin_users_id", "admin_users", ["id"])
        op.create_index("ix_admin_users_email", "admin_users", ["email"], unique=True)

    if not has_table("jobs"):
        op.create_table(
            "jobs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("department", sa.String(120)),
            sa.Column("location", sa.String(200)),
            sa.Column("employment_type", sa.String(50)),
            sa.Column("description", sa.Text, nullable=False),
            sa.Column("required_skills", sa.JSON),
            sa.Column("status", sa.String(20), nullable=False, server_default="open"),
            sa.Column("created_by", sa.Integer, sa.ForeignKey("admin_users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime),
            sa.Column("updated_at", sa.DateTime),
        )
        op.create_index("ix_jobs_id", "jobs", ["id"])
        op.create_index("ix_jobs_status", "jobs", ["status"])

    # ---- candidates: new columns ------------------------------------------------------------
    for col in (
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(32)),
        sa.Column("location", sa.String(200)),
        sa.Column("experience_years", sa.Float),
        sa.Column("skills", sa.JSON),
        sa.Column("resume_content_type", sa.String(100)),
        sa.Column("resume_size_bytes", sa.Integer),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("interview_status", sa.String(30), nullable=False, server_default="applied"),
        sa.Column("interview_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("interview_score", sa.Float),
        sa.Column("invite_token", sa.String(64)),
        sa.Column("updated_at", sa.DateTime),
    ):
        add_column_if_missing("candidates", col)

    # ---- interviews: new columns ------------------------------------------------------------
    for col in (
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime),
    ):
        add_column_if_missing("interviews", col)

    # ---- remaining new tables ---------------------------------------------------------------
    if not has_table("interview_turns"):
        op.create_table(
            "interview_turns",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("interview_id", sa.Integer, sa.ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False),
            sa.Column("turn_index", sa.Integer, nullable=False),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("topic", sa.String(200)),
            sa.Column("move", sa.String(40)),
            sa.Column("learned", sa.Text),
            sa.Column("created_at", sa.DateTime),
            sa.UniqueConstraint("interview_id", "turn_index", name="uq_interview_turn_index"),
        )
        op.create_index("ix_interview_turns_interview_id", "interview_turns", ["interview_id"])

    if not has_table("evaluations"):
        op.create_table(
            "evaluations",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("interview_id", sa.Integer, sa.ForeignKey("interviews.id", ondelete="CASCADE"),
                      nullable=False, unique=True),
            sa.Column("candidate_id", sa.Integer, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
            sa.Column("overall_score", sa.Float),
            sa.Column("recommendation", sa.String(30)),
            sa.Column("summary", sa.Text),
            sa.Column("strengths", sa.JSON),
            sa.Column("concerns", sa.JSON),
            sa.Column("criteria", sa.JSON),
            sa.Column("model", sa.String(100)),
            sa.Column("created_at", sa.DateTime),
            sa.Column("updated_at", sa.DateTime),
        )
        op.create_index("ix_evaluations_candidate_id", "evaluations", ["candidate_id"])

    if not has_table("notification_records"):
        op.create_table(
            "notification_records",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("candidate_id", sa.Integer, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
            sa.Column("interview_id", sa.Integer, sa.ForeignKey("interviews.id", ondelete="SET NULL")),
            sa.Column("channel", sa.String(10), nullable=False),
            sa.Column("kind", sa.String(30), nullable=False),
            sa.Column("recipient", sa.String(255), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
            sa.Column("provider", sa.String(40)),
            sa.Column("provider_message_id", sa.String(200)),
            sa.Column("error", sa.Text),
            sa.Column("triggered_by", sa.Integer, sa.ForeignKey("admin_users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime),
            sa.Column("sent_at", sa.DateTime),
        )
        op.create_index("ix_notification_records_candidate_id", "notification_records", ["candidate_id"])
        op.create_index("ix_notification_records_status", "notification_records", ["status"])
        op.create_index("ix_notification_candidate_created", "notification_records", ["candidate_id", "created_at"])

    # ---- indexes on existing / new columns --------------------------------------------------
    create_index_if_missing("ix_candidates_email", "candidates", ["email"])
    create_index_if_missing("ix_candidates_job_id", "candidates", ["job_id"])
    create_index_if_missing("ix_candidates_interview_status", "candidates", ["interview_status"])
    create_index_if_missing("ix_candidates_invite_token", "candidates", ["invite_token"], unique=True)
    create_index_if_missing("ix_interviews_candidate_id", "interviews", ["candidate_id"])
    create_index_if_missing("ix_interviews_job_id", "interviews", ["job_id"])
    create_index_if_missing("ix_interviews_status", "interviews", ["status"])

    # ---- back-fill existing rows (never overwrites data that is already set) -----------------
    bind.execute(sa.text(
        "UPDATE interviews SET attempt_number = 1 WHERE attempt_number IS NULL"))
    bind.execute(sa.text(
        "UPDATE candidates SET interview_attempts = "
        "(SELECT COUNT(*) FROM interviews i WHERE i.candidate_id = candidates.id) "
        "WHERE interview_attempts = 0"))
    bind.execute(sa.text(
        "UPDATE candidates SET interview_status = 'completed' WHERE interview_status = 'applied' AND EXISTS "
        "(SELECT 1 FROM interviews i WHERE i.candidate_id = candidates.id "
        "AND i.status IN ('finished', 'ended_early'))"))
    bind.execute(sa.text(
        "UPDATE candidates SET interview_status = 'in_progress' WHERE interview_status = 'applied' AND EXISTS "
        "(SELECT 1 FROM interviews i WHERE i.candidate_id = candidates.id AND i.status = 'running')"))
    rows = bind.execute(sa.text("SELECT id FROM candidates WHERE invite_token IS NULL")).fetchall()
    for (candidate_id,) in rows:
        bind.execute(sa.text("UPDATE candidates SET invite_token = :t WHERE id = :i"),
                     {"t": secrets.token_urlsafe(32), "i": candidate_id})
    # Mirror existing transcripts into interview_turns so admin queries work for historical interviews too.
    interviews = bind.execute(sa.text("SELECT id, transcript FROM interviews")).fetchall()
    import json
    turns = sa.table("interview_turns", sa.column("interview_id"), sa.column("turn_index"), sa.column("role"),
                     sa.column("text"), sa.column("topic"), sa.column("move"), sa.column("learned"))
    for interview_id, transcript in interviews:
        if isinstance(transcript, str):
            try:
                transcript = json.loads(transcript)
            except ValueError:
                transcript = None
        if not transcript:
            continue
        exists = bind.execute(sa.text("SELECT 1 FROM interview_turns WHERE interview_id = :i LIMIT 1"),
                              {"i": interview_id}).first()
        if exists:
            continue
        op.bulk_insert(turns, [
            {"interview_id": interview_id, "turn_index": idx, "role": t.get("role", "assistant"),
             "text": t.get("text", ""), "topic": (t.get("topic") or "")[:200], "move": t.get("move"),
             "learned": t.get("learned")}
            for idx, t in enumerate(transcript) if isinstance(t, dict) and t.get("text")
        ])


def downgrade() -> None:
    # Additive migration: downgrading would destroy data (jobs, evaluations, notification history...).
    # Restore from a Cloud SQL backup instead.
    raise RuntimeError("0002 is not reversible automatically; restore from backup if you must roll back.")
