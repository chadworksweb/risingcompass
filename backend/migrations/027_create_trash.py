"""Trash — soft-delete destination for anything removed via the admin DB.

Nothing is ever nuked. When an admin clicks delete, the full row gets
serialized into this table as JSON (both the row itself and any related
junction rows we care about) along with a required reason and timestamp.

The original row IS removed from its table — downstream polymorphic
references (song_artists, calibration_runs, song_slugs, etc.) become
dangling but harmless; queries that depend on the song row will just
return nothing. Recovery is possible by re-inserting the snapshot; that
tool isn't built yet.

For pre-built views of what's been trashed, the table is queryable from
the admin DB explorer like any other.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS trash (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_table VARCHAR(40) NOT NULL,
            original_id INTEGER NOT NULL,
            title TEXT,
            artist TEXT,
            row_data TEXT NOT NULL,
            related_data TEXT,
            reason TEXT NOT NULL,
            deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_trash_orig "
        "ON trash(original_table, original_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_trash_deleted_at "
        "ON trash(deleted_at DESC)"
    ))
