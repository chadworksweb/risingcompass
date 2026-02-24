"""Drop duplicated classification columns from reading_songs.

SQLite can't ALTER DROP COLUMN — recreate the table with only:
id, reading_id, compass_song_id, title, artist, position, chart_source.

Explicit column names per project convention (never SELECT *).
"""

from sqlalchemy import text


def up(conn):
    # Create slim replacement table
    conn.execute(text("""
        CREATE TABLE reading_songs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reading_id INTEGER NOT NULL REFERENCES daily_readings(id),
            compass_song_id INTEGER REFERENCES compass_songs(id),
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            position INTEGER NOT NULL,
            chart_source TEXT DEFAULT 'spotify'
        )
    """))

    # Copy data with explicit column names
    conn.execute(text("""
        INSERT INTO reading_songs_new (id, reading_id, compass_song_id, title, artist, position, chart_source)
        SELECT id, reading_id, compass_song_id, title, artist, position, chart_source
        FROM reading_songs
    """))

    # Swap tables
    conn.execute(text("DROP TABLE reading_songs"))
    conn.execute(text("ALTER TABLE reading_songs_new RENAME TO reading_songs"))

    # Recreate indexes
    conn.execute(text("CREATE INDEX ix_reading_songs_reading_id ON reading_songs(reading_id)"))
    conn.execute(text("CREATE INDEX ix_reading_songs_compass_song_id ON reading_songs(compass_song_id)"))
