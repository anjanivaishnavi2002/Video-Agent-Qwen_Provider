"""Candidate accounts, job JD files, credit wallet + ledger, recording unlock. Additive and idempotent."""
import sqlalchemy as sa

from app.db.migration_helpers import add_column_if_missing, create_index_if_missing, has_table
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("candidate_accounts"):
        op.create_table(
            "candidate_accounts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("full_name", sa.String(200), nullable=False),
            sa.Column("phone", sa.String(32)),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("token_version", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_login_at", sa.DateTime),
            sa.Column("created_at", sa.DateTime),
        )
    create_index_if_missing("ix_candidate_accounts_id", "candidate_accounts", ["id"])
    create_index_if_missing("ix_candidate_accounts_email", "candidate_accounts", ["email"], unique=True)

    for col in (sa.Column("jd_file_path", sa.String), sa.Column("jd_file_name", sa.String(200)),
                sa.Column("jd_content_type", sa.String(100)), sa.Column("jd_size_bytes", sa.Integer)):
        add_column_if_missing("jobs", col)

    add_column_if_missing("candidates", sa.Column("account_id", sa.Integer, sa.ForeignKey(
        "candidate_accounts.id", ondelete="SET NULL")))
    create_index_if_missing("ix_candidates_account_id", "candidates", ["account_id"])

    add_column_if_missing("interviews", sa.Column("video_unlocked_at", sa.DateTime))
    add_column_if_missing("interviews", sa.Column("video_unlocked_by", sa.Integer, sa.ForeignKey(
        "admin_users.id", ondelete="SET NULL")))

    if not has_table("credit_wallet"):
        op.create_table(
            "credit_wallet",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("balance", sa.Integer, nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime),
        )
    if not has_table("credit_ledger"):
        op.create_table(
            "credit_ledger",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("delta", sa.Integer, nullable=False),
            sa.Column("reason", sa.String(30), nullable=False),
            sa.Column("note", sa.String(300)),
            sa.Column("interview_id", sa.Integer, sa.ForeignKey("interviews.id", ondelete="SET NULL")),
            sa.Column("candidate_id", sa.Integer, sa.ForeignKey("candidates.id", ondelete="SET NULL")),
            sa.Column("admin_id", sa.Integer, sa.ForeignKey("admin_users.id", ondelete="SET NULL")),
            sa.Column("balance_after", sa.Integer, nullable=False),
            sa.Column("unlock_key", sa.String(60), unique=True),
            sa.Column("created_at", sa.DateTime),
        )
        create_index_if_missing("ix_credit_ledger_interview_id", "credit_ledger", ["interview_id"])
        create_index_if_missing("ix_credit_ledger_candidate_id", "credit_ledger", ["candidate_id"])


def downgrade() -> None:
    pass   # never drop data automatically
