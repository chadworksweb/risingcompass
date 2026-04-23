"""Unique case-insensitive (title, artist) index on every song table.

This is the structural guarantee that no two rows in the same song table
can share a (case-insensitive) title + artist. Complements the
application-layer dedup in `services.calibration_corpus.get_or_create_song`
— the helper handles graceful "already exists" returns; the index is the
belt-and-suspenders safety net so any future ingestion path (written
without the helper) cannot reintroduce duplicates.

Migration prerequisites: all existing duplicates must already be merged.
Those were cleaned via the admin merge endpoint in the same session that
shipped the get_or_create_song helper (April 23 2026).

Scope: intra-table only. A song can legitimately exist in multiple
tables representing different pipeline stages (submitted → library →
compass). The badge lookup's source precedence handles cross-table
canonical selection.
"""

from sqlalchemy import text


def up(conn):
    # libSQL (SQLite-based) supports functional indexes. lower() makes the
    # uniqueness case-insensitive so "Song" and "song" collide.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_compass_songs_title_artist_ci "
        "ON compass_songs (lower(title), lower(artist))"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_library_songs_title_artist_ci "
        "ON library_songs (lower(title), lower(artist))"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_submitted_songs_title_artist_ci "
        "ON submitted_songs (lower(title), lower(artist))"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_stream_songs_title_artist_ci "
        "ON stream_songs (lower(title), lower(artist))"
    ))
