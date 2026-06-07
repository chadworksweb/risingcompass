"""Rename the listener-effects columns to listener_effects_prose and align
releases.societal_prose -> societal_effects_prose.

Part of the effects -> listener_effects refactor (2026-06-07). The societal
columns on `songs` already carried the "societal" name and are left untouched;
only the bare "effects"/"societal_prose" names are renamed for a symmetric
listener_effects_prose / societal_effects_prose pair. Idempotent: each rename
is guarded on information_schema so a re-run (or a fresh install where
create_all already made the new names) is a no-op. PG-compatible.
"""

from sqlalchemy import text

RENAMES = [
    ("songs", "effects_prose", "listener_effects_prose"),
    ("songs", "prior_effects_prose", "prior_listener_effects_prose"),
    ("releases", "effects_prose", "listener_effects_prose"),
    ("releases", "societal_prose", "societal_effects_prose"),
]


def _has_col(conn, table, col):
    return conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name=:t AND column_name=:c AND table_schema='public'"
    ), {"t": table, "c": col}).fetchone() is not None


def up(conn):
    for table, old, new in RENAMES:
        if _has_col(conn, table, old) and not _has_col(conn, table, new):
            conn.execute(text(f'ALTER TABLE {table} RENAME COLUMN {old} TO {new}'))
