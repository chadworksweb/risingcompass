"""Add compass_song_id FK to agent_draft_songs so draft responses expose the correct ID for calibration."""

from sqlalchemy import text


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def up(conn):
    if _column_exists(conn, "agent_draft_songs", "compass_song_id"):
        return

    conn.execute(text(
        "ALTER TABLE agent_draft_songs ADD COLUMN compass_song_id INTEGER REFERENCES compass_songs(id)"
    ))
