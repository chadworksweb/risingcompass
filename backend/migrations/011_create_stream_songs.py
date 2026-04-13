"""Create stream_songs table for CL Stream."""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS stream_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            note TEXT NOT NULL,
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
