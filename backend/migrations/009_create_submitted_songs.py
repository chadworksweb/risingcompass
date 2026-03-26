"""Create submitted_songs table for Lyrical Charger crowd-submitted classifications."""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS submitted_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            artist TEXT,
            rubric_color TEXT NOT NULL,
            charge_value INTEGER,
            contaminated BOOLEAN DEFAULT 0,
            contamination_note TEXT,
            charge_summary TEXT,
            confidence REAL,
            source VARCHAR(20) DEFAULT 'paste_lyrics',
            submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))
