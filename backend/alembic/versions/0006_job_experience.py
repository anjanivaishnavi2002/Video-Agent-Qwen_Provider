"""Job process type and experience band. Additive and idempotent."""
import sqlalchemy as sa

from app.db.migration_helpers import add_column_if_missing

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("jobs", sa.Column("process_type", sa.String(20)))
    add_column_if_missing("jobs", sa.Column("experience_min", sa.Integer))
    add_column_if_missing("jobs", sa.Column("experience_max", sa.Integer))


def downgrade() -> None:
    pass
