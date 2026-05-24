"""Lightweight versioned migration runner for SQLite schema changes.

Base.metadata.create_all() handles fresh installs.  Migrations handle
ALTER TABLE on existing tables (SQLite lacks IF NOT EXISTS for columns).
"""

import importlib
import logging
import os
import re

from sqlalchemy import text

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")


def _ensure_schema_version(conn):
    """Create the schema_version table if it doesn't exist."""
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    ))


def _get_current_version(conn) -> int:
    """Return the highest applied migration version, or 0 if none."""
    row = conn.execute(text("SELECT MAX(version) FROM schema_version")).fetchone()
    return row[0] or 0


def _discover_migrations() -> list[tuple[int, str, str]]:
    """Find all NNN_description.py files in the migrations directory.

    Returns sorted list of (version, module_name, file_path).
    """
    migrations = []
    migrations_dir = os.path.normpath(MIGRATIONS_DIR)
    if not os.path.isdir(migrations_dir):
        return migrations

    pattern = re.compile(r"^(\d{3})_(\w+)\.py$")
    for filename in sorted(os.listdir(migrations_dir)):
        match = pattern.match(filename)
        if match:
            version = int(match.group(1))
            module_name = filename[:-3]  # strip .py
            filepath = os.path.join(migrations_dir, filename)
            migrations.append((version, module_name, filepath))

    return migrations


def run_migrations(engine):
    """Discover and apply pending migrations in order."""
    with engine.begin() as conn:
        _ensure_schema_version(conn)
        current = _get_current_version(conn)

    migrations = _discover_migrations()
    applied = 0

    for version, module_name, filepath in migrations:
        if version <= current:
            continue

        # Import the migration module
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if not hasattr(mod, "up"):
            logger.warning("Migration %03d has no up() function, skipping", version)
            continue

        # Run in a transaction
        with engine.begin() as conn:
            try:
                mod.up(conn)
                conn.execute(
                    text("INSERT INTO schema_version (version) VALUES (:v)"),
                    {"v": version},
                )
                applied += 1
                logger.info("Applied migration %03d_%s", version, module_name)
            except Exception:
                logger.exception("Migration %03d_%s FAILED — rolling back", version, module_name)
                raise

    if applied:
        logger.info("Migrations complete: %d applied (now at version %03d)", applied, version)
    else:
        logger.info("Migrations: up to date (version %03d)", current)
