"""Give songs.created_at a DB-level default + backfill the NULL rows.

Root cause: songs.created_at carried only a client-side SQLAlchemy default
(default=datetime.utcnow). That default fires on ORM inserts only. The unified
write chokepoint, song_sync.upsert_unified_song, inserts via raw SQL
(text("INSERT INTO songs ...")) and never listed created_at -- so every song
created after the song-entity renovation (2026-06-05, when writes moved to that
raw path) landed created_at NULL.

Fix: add a server_default so the column self-stamps on any insert path, then
backfill existing NULL rows from the earliest calibration_runs.run_at (the true
first-calibration time; fallback: earliest song_ingestions.created_at). Rows with
no run/ingestion timestamp stay NULL -- there is no real time to assign.

Naive-UTC to match the app's datetime.utcnow() convention (the column is a
DateTime without tz). PG-compatible (063+); create_all() builds the default on
fresh installs from the model server_default.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ALTER COLUMN created_at SET DEFAULT (now() at time zone 'utc')"
    ))
    # Backfill NULLs from the earliest calibration run for the song.
    conn.execute(text(
        "UPDATE songs s SET created_at = sub.ts "
        "FROM (SELECT song_id, MIN(run_at) AS ts FROM calibration_runs "
        "      WHERE song_id IS NOT NULL GROUP BY song_id) sub "
        "WHERE s.id = sub.song_id AND s.created_at IS NULL AND sub.ts IS NOT NULL"
    ))
    # Fallback for any still-NULL: earliest ingestion record.
    conn.execute(text(
        "UPDATE songs s SET created_at = sub.ts "
        "FROM (SELECT song_id, MIN(created_at) AS ts FROM song_ingestions "
        "      WHERE created_at IS NOT NULL GROUP BY song_id) sub "
        "WHERE s.id = sub.song_id AND s.created_at IS NULL AND sub.ts IS NOT NULL"
    ))
