"""Migrate local SQLite database to Turso via HTTP API.

Usage:
    cd backend
    .venv/Scripts/python scripts/migrate_to_turso.py

Reads rising_compass.db from data/, pushes schema + data to Turso.
"""

import json
import sqlite3
import sys
from pathlib import Path

import httpx

# --- Config ---
TURSO_URL = "libsql://risingcompass-crystopaforge.aws-us-east-1.turso.io"
TURSO_TOKEN = "REDACTED-TURSO-TOKEN"

# Convert libsql:// to https:// for HTTP API
HTTP_URL = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rising_compass.db"

# Tables in dependency order (parents before children)
TABLE_ORDER = [
    "schema_version",
    "compass_songs",
    "daily_readings",
    "reading_songs",
    "weekly_album_readings",
    "weekly_album_entries",
    "album_deep_dives",
    "album_tracks",
    "library_songs",
    "collections",
    "recommendations",
    "agent_drafts",
    "agent_draft_songs",
    "submitted_songs",
    "misread_submissions",
    "misread_bans",
]

BATCH_SIZE = 50  # Rows per HTTP request


def turso_execute(client: httpx.Client, statements: list[dict]) -> dict:
    """Send a batch of statements to Turso's HTTP pipeline API."""
    body = {
        "requests": [
            {"type": "execute", "stmt": stmt} for stmt in statements
        ] + [{"type": "close"}]
    }
    resp = client.post(HTTP_URL, json=body)
    if resp.status_code != 200:
        print(f"ERROR {resp.status_code}: {resp.text}")
        sys.exit(1)
    return resp.json()


def get_create_statements(conn: sqlite3.Connection) -> list[str]:
    """Extract CREATE TABLE statements from SQLite."""
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL ORDER BY name"
    )
    return [row[0] for row in cursor.fetchall()]


def get_table_data(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    """Get column names and all rows for a table."""
    cursor = conn.execute(f"SELECT * FROM [{table}]")
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    return columns, rows


def value_to_turso(val):
    """Convert a Python value to Turso API value format."""
    if val is None:
        return {"type": "null"}
    elif isinstance(val, int):
        return {"type": "integer", "value": str(val)}
    elif isinstance(val, float):
        return {"type": "float", "value": val}
    elif isinstance(val, bytes):
        return {"type": "blob", "base64": __import__("base64").b64encode(val).decode()}
    else:
        return {"type": "text", "value": str(val)}


def main():
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        print("Run db-pull.sh first to get the production database.")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys=OFF")

    # Check what tables exist
    existing = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    print(f"Source tables: {sorted(existing)}")

    client = httpx.Client(
        headers={"Authorization": f"Bearer {TURSO_TOKEN}"},
        timeout=30.0,
    )

    # Step 1: Create schema
    print("\n--- Creating schema ---")
    create_stmts = get_create_statements(conn)
    # Also enable foreign keys
    statements = [{"sql": "PRAGMA foreign_keys=OFF"}]
    for sql in create_stmts:
        statements.append({"sql": sql})
        print(f"  {sql[:80]}...")

    result = turso_execute(client, statements)
    # Check for errors (but ignore "table already exists")
    for i, r in enumerate(result.get("results", [])):
        if r.get("type") == "error":
            err = r.get("error", {}).get("message", "")
            if "already exists" not in err:
                print(f"  Schema error: {err}")

    print("Schema created.")

    # Step 2: Insert data table by table
    print("\n--- Inserting data ---")
    total_rows = 0

    for table in TABLE_ORDER:
        if table not in existing:
            print(f"  {table}: skipped (not in source)")
            continue

        columns, rows = get_table_data(conn, table)
        if not rows:
            print(f"  {table}: 0 rows")
            continue

        # Build parameterized INSERT
        placeholders = ", ".join(["?"] * len(columns))
        col_list = ", ".join([f"[{c}]" for c in columns])
        insert_sql = f"INSERT INTO [{table}] ({col_list}) VALUES ({placeholders})"

        # Send in batches
        for batch_start in range(0, len(rows), BATCH_SIZE):
            batch = rows[batch_start:batch_start + BATCH_SIZE]
            statements = []
            for row in batch:
                args = [value_to_turso(v) for v in row]
                statements.append({"sql": insert_sql, "args": args})

            result = turso_execute(client, statements)

            # Check for errors
            for r in result.get("results", []):
                if r.get("type") == "error":
                    err = r.get("error", {}).get("message", "")
                    print(f"  ERROR in {table}: {err}")

        total_rows += len(rows)
        print(f"  {table}: {len(rows)} rows")

    print(f"\n--- Done. {total_rows} total rows migrated. ---")

    # Step 3: Re-enable foreign keys and verify
    print("\n--- Verifying ---")
    result = turso_execute(client, [
        {"sql": "PRAGMA foreign_keys=ON"},
        {"sql": "SELECT COUNT(*) FROM compass_songs"},
        {"sql": "SELECT COUNT(*) FROM daily_readings"},
    ])
    for r in result.get("results", []):
        if r.get("type") == "ok":
            resp = r.get("response", {}).get("result", {})
            rows = resp.get("rows", [])
            if rows:
                print(f"  Count: {rows[0][0].get('value', '?')}")

    conn.close()
    client.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
