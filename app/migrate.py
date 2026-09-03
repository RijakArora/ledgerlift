"""Minimal, idempotent schema migration.

SQLAlchemy's create_all() creates missing *tables* but never alters
existing ones, so columns added to a model after a table already exists
are silently absent in production. That fails at query time, not at
startup, which is the worst place to find out.

This runs on boot and adds any missing columns. Deliberately tiny - a
full migration tool (Alembic) is the right answer once schema changes
get complex, but is overkill while the schema is this small.
"""
import logging

from sqlalchemy import inspect, text

log = logging.getLogger(__name__)

# table -> {column: SQL type}. Only additive changes belong here.
EXPECTED_COLUMNS = {
    "documents": {
        "client_id": "INTEGER",
        "edited_at": "TIMESTAMP",
        "duplicate_of_id": "INTEGER",
    },
}


def ensure_schema(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table, columns in EXPECTED_COLUMNS.items():
        if table not in existing_tables:
            continue  # create_all() will build it with every column
        present = {c["name"] for c in inspector.get_columns(table)}
        for column, sql_type in columns.items():
            if column in present:
                continue
            # Both Postgres and modern SQLite accept plain ADD COLUMN.
            stmt = f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
                log.info("schema: added %s.%s", table, column)
            except Exception as e:  # noqa: BLE001
                # Never take the app down over a migration race; log loudly.
                log.warning("schema: could not add %s.%s (%s)", table, column, e)
