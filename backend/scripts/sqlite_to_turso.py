"""One-shot migration: copy local SQLite file to Turso DB.

Usage:
    .venv/Scripts/python.exe scripts/sqlite_to_turso.py <sqlite_path> <turso_url> <auth_token>
"""

import sys
import sqlite3
import libsql

SKIP_TABLES = {"sqlite_sequence"}  # libSQL manages internally


def main(sqlite_path: str, turso_url: str, auth_token: str) -> None:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    dst = libsql.connect(database="rc_migration_local.db", sync_url=turso_url, auth_token=auth_token)
    dst.sync()

    # Wipe destination first (idempotent re-runs)
    existing = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in existing:
        if t in SKIP_TABLES or t.startswith("sqlite_") or t.startswith("libsql_"):
            continue
        dst.execute(f"DROP TABLE IF EXISTS {t}")
    dst.commit()

    # Copy schema (tables, then indexes)
    table_defs = src.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL ORDER BY name"
    ).fetchall()
    for name, sql in table_defs:
        if name in SKIP_TABLES:
            continue
        dst.execute(sql)
    dst.commit()

    index_defs = src.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
    ).fetchall()
    for name, sql in index_defs:
        dst.execute(sql)
    dst.commit()

    # Copy data with FK checks off (insertion order across tables can violate FKs mid-load)
    dst.execute("PRAGMA foreign_keys = OFF")
    summary = []
    for name, _ in table_defs:
        if name in SKIP_TABLES:
            continue
        rows = src.execute(f"SELECT * FROM {name}").fetchall()
        if not rows:
            summary.append((name, 0))
            continue
        cols = rows[0].keys()
        col_list = ",".join(f'"{c}"' for c in cols)
        placeholders = ",".join("?" * len(cols))
        insert_sql = f"INSERT INTO {name} ({col_list}) VALUES ({placeholders})"
        for row in rows:
            dst.execute(insert_sql, tuple(row))
        summary.append((name, len(rows)))
    dst.commit()
    dst.execute("PRAGMA foreign_keys = ON")
    dst.sync()

    print("\n=== Migration complete ===")
    for name, count in summary:
        print(f"  {name}: {count} rows")
    print(f"\nTotal tables: {len(summary)}")
    print(f"Total rows: {sum(c for _, c in summary)}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
