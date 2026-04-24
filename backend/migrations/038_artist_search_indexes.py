"""Indexes that support the /api/artists/search hot path.

The search endpoint does three kinds of LIKE scans:

  1. `lower(artists.name) LIKE '%q%'` — primary indexed-artist match
  2. `lower(artists.name) = lower(x)` — per-artist count joins
  3. `lower(<song_table>.artist) LIKE '%q%'` / `= lower(x)` — song counts and
     unindexed-name discovery across compass_songs, library_songs, submitted_songs

SQLite can't use a functional index for leading-wildcard LIKE, but it *can*
use it for equality lookups (`=`) and for partial LIKE with STARTS-WITH.
Migration 037 already created `(lower(title), lower(artist))` unique indexes
on the song tables; those help equality on (title, artist) together but not
equality/LIKE on `lower(artist)` alone.

Adding dedicated `lower(artist)` / `lower(name)` indexes:
  - Makes the per-artist equality joins in the aggregated search SQL O(log n)
    instead of O(n) full scans.
  - Speeds up the unindexed-names UNION query's `=` branches.

The primary-artist LIKE with leading `%` still scans, but the artists table
is tiny (a few thousand rows) compared to the song tables (tens of thousands
each), so that scan is cheap.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_artists_name_lower "
        "ON artists (lower(name))"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_compass_songs_artist_lower "
        "ON compass_songs (lower(artist))"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_library_songs_artist_lower "
        "ON library_songs (lower(artist))"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_submitted_songs_artist_lower "
        "ON submitted_songs (lower(artist))"
    ))
