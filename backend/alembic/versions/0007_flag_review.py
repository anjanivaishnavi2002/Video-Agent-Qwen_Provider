"""A reviewer's decision on the proctoring flags of an interview. Additive and idempotent."""
import sqlalchemy as sa

from app.db.migration_helpers import add_column_if_missing

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("interviews", sa.Column("flag_review", sa.JSON))


def downgrade() -> None:
    pass
