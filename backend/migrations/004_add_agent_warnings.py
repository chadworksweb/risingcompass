"""Add agent_warnings column to agent_drafts."""

from sqlalchemy import text


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def up(conn):
    if _column_exists(conn, "agent_drafts", "agent_warnings"):
        return

    conn.execute(text("ALTER TABLE agent_drafts ADD COLUMN agent_warnings TEXT"))
