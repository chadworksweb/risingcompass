"""Cut a fresh Postgres baseline from the current SQLAlchemy models.

The numbered migrations (001-062) are SQLite-dialect and are NOT replayed on
Postgres. Instead we build the current schema in one shot with
Base.metadata.create_all(), then stamp schema_version up to the highest
existing migration so the runner treats them all as already applied and only
runs 063+ going forward. See RISING-COMPASS-POSTGRES-MIGRATION.md (Cutover 2).

Idempotent: create_all skips existing tables; the stamp uses INSERT .. ON
CONFLICT DO NOTHING.

Run (with the SSH tunnel up):
    .venv\\Scripts\\python.exe scripts\\pg_baseline.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

import app.models  # noqa: F401 - registers every model on Base.metadata
from app.database import Base, engine

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations"
)


def _discovered_versions() -> list[int]:
    pat = re.compile(r"^(\d{3})_\w+\.py$")
    versions = []
    for name in os.listdir(MIGRATIONS_DIR):
        m = pat.match(name)
        if m:
            versions.append(int(m.group(1)))
    return sorted(versions)


def main():
    print(f"Engine: {engine.url.render_as_string(hide_password=True)}")

    Base.metadata.create_all(engine)
    print(f"create_all: {len(Base.metadata.tables)} model tables ensured")

    versions = _discovered_versions()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        for v in versions:
            conn.execute(
                text(
                    "INSERT INTO schema_version (version) VALUES (:v) "
                    "ON CONFLICT (version) DO NOTHING"
                ),
                {"v": v},
            )
        current = conn.execute(
            text("SELECT MAX(version) FROM schema_version")
        ).scalar()

    print(f"schema_version stamped: {len(versions)} versions, now at {current:03d}")


if __name__ == "__main__":
    main()
