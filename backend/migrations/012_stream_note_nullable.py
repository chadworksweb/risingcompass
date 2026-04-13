"""Make stream_songs.note nullable (was NOT NULL)."""

from sqlalchemy import text


def up(conn):
    # SQLite doesn't support ALTER COLUMN — recreate table with note nullable
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS stream_songs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            note TEXT,
            source_url TEXT,
            source_platform VARCHAR(30),
            rubric_color TEXT,
            charge_value INTEGER,
            contaminated BOOLEAN DEFAULT 0,
            contamination_note TEXT,
            charge_summary TEXT,
            confidence REAL,
            status VARCHAR(20) DEFAULT 'calibrated',
            promoted_to VARCHAR(20),
            created_at DATETIME DEFAULT (datetime('now'))
        )
    """))
    conn.execute(text("""
        INSERT INTO stream_songs_new
        SELECT id, title, artist, note, source_url, source_platform,
               rubric_color, charge_value, contaminated, contamination_note,
               charge_summary, confidence, status, promoted_to, created_at
        FROM stream_songs
    """))
    conn.execute(text("DROP TABLE stream_songs"))
    conn.execute(text("ALTER TABLE stream_songs_new RENAME TO stream_songs"))
