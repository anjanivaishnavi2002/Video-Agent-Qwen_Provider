"""Idempotent DDL helpers for Alembic migrations: every migration can run safely on a database that already has
some of these objects (for example one created by the old `create_all` start-up code). Nothing here drops data."""
import sqlalchemy as sa
from alembic import op


def _inspector():
    return sa.inspect(op.get_bind())


def has_table(name: str) -> bool:
    return _inspector().has_table(name)


def has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in _inspector().get_columns(table))


def add_column_if_missing(table: str, column: sa.Column) -> None:
    if not has_column(table, column.name):
        op.add_column(table, column)


def create_index_if_missing(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    if not any(i["name"] == name for i in _inspector().get_indexes(table)):
        op.create_index(name, table, columns, unique=unique)
