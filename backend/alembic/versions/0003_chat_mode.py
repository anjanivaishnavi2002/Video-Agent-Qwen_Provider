"""Chat (written skills) assessment mode + tab-switch counter. Additive and idempotent: no data is changed or removed."""
import sqlalchemy as sa

from app.db.migration_helpers import add_column_if_missing

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("interviews", sa.Column("mode", sa.String(10), nullable=False, server_default="voice"))
    add_column_if_missing("interviews", sa.Column("tab_switch_count", sa.Integer, nullable=False, server_default="0"))
    add_column_if_missing("interviews", sa.Column("chat_tasks", sa.JSON))
    add_column_if_missing("interviews", sa.Column("chat_work", sa.JSON))


def downgrade() -> None:
    pass   # never drop interview data automatically
