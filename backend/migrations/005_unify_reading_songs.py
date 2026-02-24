"""Add compass_song_id FK to reading_songs and label to daily_readings.

Non-destructive: old columns stay, existing code still works.
Backfills compass_song_id by matching LOWER(title), LOWER(artist).
Backfills label as 'reading_YYYY-MM-DD'.
"""

from sqlalchemy import text


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def up(conn):
    # --- reading_songs: add compass_song_id FK ---
    if not _column_exists(conn, "reading_songs", "compass_song_id"):
        conn.execute(text(
            "ALTER TABLE reading_songs ADD COLUMN compass_song_id INTEGER REFERENCES compass_songs(id)"
        ))

    # Backfill: match by LOWER(title), LOWER(artist), pick highest compass_songs.id when dupes exist
    conn.execute(text("""
        UPDATE reading_songs
        SET compass_song_id = (
            SELECT cs.id
            FROM compass_songs cs
            WHERE LOWER(cs.title) = LOWER(reading_songs.title)
              AND LOWER(cs.artist) = LOWER(reading_songs.artist)
            ORDER BY cs.id DESC
            LIMIT 1
        )
        WHERE compass_song_id IS NULL
    """))

    # --- daily_readings: add label ---
    if not _column_exists(conn, "daily_readings", "label"):
        conn.execute(text("ALTER TABLE daily_readings ADD COLUMN label TEXT"))

    # Backfill labels as 'reading_YYYY-MM-DD'
    conn.execute(text("""
        UPDATE daily_readings
        SET label = 'reading_' || date
        WHERE label IS NULL
    """))
