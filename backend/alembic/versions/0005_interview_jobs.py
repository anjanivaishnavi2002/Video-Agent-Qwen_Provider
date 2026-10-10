"""One interview per candidate account, attached to every job they applied to. Additive and idempotent."""
import sqlalchemy as sa

from app.db.migration_helpers import create_index_if_missing, has_table
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("interview_jobs"):
        op.create_table(
            "interview_jobs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("interview_id", sa.Integer, sa.ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False),
            sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("candidate_id", sa.Integer, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime),
            sa.UniqueConstraint("interview_id", "job_id", name="uq_interview_job"),
        )
    create_index_if_missing("ix_interview_jobs_interview_id", "interview_jobs", ["interview_id"])
    create_index_if_missing("ix_interview_jobs_job_id", "interview_jobs", ["job_id"])
    create_index_if_missing("ix_interview_jobs_candidate_id", "interview_jobs", ["candidate_id"])
    # Backfill: every existing job-bound interview is attached to its job.
    op.execute(sa.text(
        "INSERT INTO interview_jobs (interview_id, job_id, candidate_id, created_at) "
        "SELECT i.id, i.job_id, i.candidate_id, i.created_at FROM interviews i "
        "WHERE i.job_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM interview_jobs x WHERE x.interview_id = i.id AND x.job_id = i.job_id)"))


def downgrade() -> None:
    pass
