"""Add label column to agent_drafts and backfill existing rows."""

from sqlalchemy import text


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def up(conn):
    if _column_exists(conn, "agent_drafts", "label"):
        return  # already present (pre-migration-framework)

    conn.execute(text("ALTER TABLE agent_drafts ADD COLUMN label TEXT"))

    # Backfill labels for existing drafts
    rows = conn.execute(
        text("SELECT id, date FROM agent_drafts ORDER BY date, id")
    ).fetchall()

    seen: dict[str, int] = {}
    for row_id, row_date in rows:
        date_str = str(row_date)
        count = seen.get(date_str, 0)
        if count == 0:
            label = f"compass_song_{date_str}_draft"
        else:
            modifier = chr(ord("a") + count)
            label = f"compass_song_{date_str}{modifier}_draft"
        seen[date_str] = count + 1
        conn.execute(
            text("UPDATE agent_drafts SET label = :label WHERE id = :id"),
            {"label": label, "id": row_id},
        )
