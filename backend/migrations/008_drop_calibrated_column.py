"""Remove the calibrated column from compass_songs.

Classification status is now derived from having all three fields:
rubric_color, charge_value, and charge_summary. The separate calibrated
flag was redundant and caused songs to be incorrectly flagged as new.
"""

from sqlalchemy import text


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def _get_columns(conn, table: str) -> list[dict]:
    """Return column info dicts from PRAGMA table_info."""
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return [{"cid": r[0], "name": r[1], "type": r[2], "notnull": r[3], "dflt": r[4], "pk": r[5]} for r in rows]


def up(conn):
    if not _column_exists(conn, "compass_songs", "calibrated"):
        return

    # Read actual schema, drop only the calibrated column
    cols = _get_columns(conn, "compass_songs")
    keep = [c for c in cols if c["name"] != "calibrated"]

    # Build column definitions for the new table
    col_defs = []
    for c in keep:
        parts = [c["name"], c["type"]]
        if c["pk"]:
            parts.append("PRIMARY KEY AUTOINCREMENT")
        else:
            if c["notnull"]:
                parts.append("NOT NULL")
            if c["dflt"] is not None:
                parts.append(f"DEFAULT {c['dflt']}")
        col_defs.append(" ".join(parts))

    col_names = ", ".join(c["name"] for c in keep)
    col_defs_str = ",\n            ".join(col_defs)

    # Disable FK checks for the table rebuild
    conn.execute(text("PRAGMA foreign_keys = OFF"))

    # Clean up partial run if the temp table already exists
    conn.execute(text("DROP TABLE IF EXISTS compass_songs_new"))

    conn.execute(text(f"""
        CREATE TABLE compass_songs_new (
            {col_defs_str}
        )
    """))
    conn.execute(text(f"""
        INSERT INTO compass_songs_new ({col_names})
        SELECT {col_names} FROM compass_songs
    """))
    conn.execute(text("DROP TABLE compass_songs"))
    conn.execute(text("ALTER TABLE compass_songs_new RENAME TO compass_songs"))

    # Re-enable FK checks
    conn.execute(text("PRAGMA foreign_keys = ON"))
