"""Song → artist attribution junction.

Existing model credits one artist per song via the `artist` text column. That
breaks for collabs (Lady Gaga & Doechi) and features (Drake feat. 21 Savage) —
artist search, artist trajectory, and per-artist song pages all miss the
non-leading artist.

This table makes song→artist N:M, with role (primary | featured) and position
for display ordering. Polymorphic `song_source` matches the rest of the
codebase (compass / library / submitted). Old rows are NOT backfilled — lazy
as-touched: new LC submits, new compass-agent approvals, and admin library
CRUD write entries here; legacy rows stay single-string until something
touches them.

Unique on (song_source, song_id, artist_id) — a song can't list the same
artist twice, but can list many artists.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            artist_id INTEGER NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'primary',
            position INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE,
            UNIQUE(song_source, song_id, artist_id)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_artists_artist "
        "ON song_artists(artist_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_artists_song "
        "ON song_artists(song_source, song_id)"
    ))
