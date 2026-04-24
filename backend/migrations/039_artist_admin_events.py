"""Audit log for admin artist operations (merge, rename).

Every merge/rename writes a row here with a snapshot of what the artist
looked like before, what it became, and a JSON breakdown of every FK
rewrite the operation performed (song_artists touched, releases moved,
song-table artist strings normalised). The row is written in the same
transaction as the mutation itself — if the commit rolls back, the event
row does too, so the log can never show an action that didn't happen.

Extensible: future "auto" events (duplicate detector, bulk cleanup) can
reuse the same table with a different event_type.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS artist_admin_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at DATETIME NOT NULL DEFAULT (datetime('now')),
            event_type TEXT NOT NULL,
            actor TEXT,

            artist_id INTEGER,
            artist_name_before TEXT NOT NULL,
            artist_slug_before TEXT NOT NULL,
            artist_name_after TEXT,
            artist_slug_after TEXT,

            target_artist_id INTEGER,
            target_artist_name TEXT,
            target_artist_slug TEXT,

            rewrites_json TEXT,
            notes TEXT
        )
        """
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_artist_admin_events_occurred "
        "ON artist_admin_events(occurred_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_artist_admin_events_artist "
        "ON artist_admin_events(artist_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_artist_admin_events_target "
        "ON artist_admin_events(target_artist_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_artist_admin_events_type "
        "ON artist_admin_events(event_type, occurred_at DESC)"
    ))
