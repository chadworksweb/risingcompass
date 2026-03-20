"""Remove the calibrated column from compass_songs.

Classification status is now derived from having all three fields:
rubric_color, charge_value, and charge_summary. The separate calibrated
flag was redundant and caused songs to be incorrectly flagged as new.
"""

from sqlalchemy import text


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def up(conn):
    if not _column_exists(conn, "compass_songs", "calibrated"):
        return

    # SQLite doesn't support DROP COLUMN before 3.35.0.
    # Rebuild the table without the calibrated column.
    conn.execute(text("""
        CREATE TABLE compass_songs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            year INTEGER NOT NULL,
            decade TEXT NOT NULL,
            chart_position INTEGER NOT NULL,
            rubric_color TEXT NOT NULL,
            charge_value INTEGER,
            contaminated BOOLEAN DEFAULT 0,
            contamination_note TEXT,
            charge_summary TEXT,
            why_classification TEXT,
            chart_source TEXT DEFAULT 'billboard_hot_100',
            instrumental BOOLEAN DEFAULT 0
        )
    """))
    conn.execute(text("""
        INSERT INTO compass_songs_new
            (id, title, artist, year, decade, chart_position, rubric_color,
             charge_value, contaminated, contamination_note, charge_summary,
             why_classification, chart_source, instrumental)
        SELECT
            id, title, artist, year, decade, chart_position, rubric_color,
            charge_value, contaminated, contamination_note, charge_summary,
            why_classification, chart_source, instrumental
        FROM compass_songs
    """))
    conn.execute(text("DROP TABLE compass_songs"))
    conn.execute(text("ALTER TABLE compass_songs_new RENAME TO compass_songs"))
