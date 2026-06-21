"""Versioned migration runner.

Base.metadata.create_all() handles fresh installs; numbered migrations/NNN_*.py
handle schema changes on existing installs.

Migrations are tracked by FILENAME identity in `schema_migrations`, NOT by a
high-water version number. This is deliberate. Rising Compass runs parallel work
tracks against ONE shared database (see CLAUDE.md "Parallel Work Tracks"), so two
tracks can independently author a migration and both pick the same "next" number.
The old runner gated on `version <= MAX(applied)`, which silently SKIPPED a
genuinely-unapplied migration whenever another track had already recorded an equal
or higher number: the migration never ran yet looked applied (this bit the
099/100 -> 102/103 renumber, and the governance-drop 130 -> 132 -> 134 renumber).
Keying on the filename means every distinct migration runs exactly once, a reused
number cannot shadow another track's file, and a late-added lower number still
applies.

`schema_version` (the legacy version-only table) is kept and read ONCE to bridge
already-applied history into the new ledger; the PG baseline tooling still stamps
it. After that bridge, the filename ledger is the sole source of truth.
"""

import importlib.util
import logging
import os
import re

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations")

# Recorded in schema_migrations once the one-time legacy bridge has run. It freezes
# the high-water mark so the bridge never re-imports a file added afterward (a
# late-added lower number must run, not be treated as already-applied).
_BASELINE_SENTINEL = "__legacy_baseline__"

_MIGRATION_RE = re.compile(r"^(\d{3})_(\w+)\.py$")


def _ensure_tables(conn):
    """Create both ledgers if absent.

    schema_version = legacy version-only history (read once for the bridge; still
    written by the PG baseline tooling). schema_migrations = the filename ledger,
    the source of truth going forward.
    """
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  version INTEGER NOT NULL,"
        "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    ))


def _legacy_max_version(conn) -> int:
    row = conn.execute(text("SELECT MAX(version) FROM schema_version")).fetchone()
    return (row[0] or 0) if row else 0


def _applied_names(conn) -> set:
    rows = conn.execute(text("SELECT name FROM schema_migrations")).fetchall()
    return {r[0] for r in rows}


def _record_name(conn, name, version):
    """Mark a migration filename as applied.

    Wrapped in a SAVEPOINT so a duplicate (a concurrent worker won the race) only
    rolls back this insert, never poisons the caller's transaction. Portable across
    SQLite and Postgres.
    """
    try:
        with conn.begin_nested():
            conn.execute(
                text("INSERT INTO schema_migrations (name, version) VALUES (:n, :v)"),
                {"n": name, "v": version},
            )
    except IntegrityError:
        pass


def _discover_migrations(migrations_dir=None):
    """Return [(version, module_name, filepath)] sorted by (version, name)."""
    migrations = []
    migrations_dir = os.path.normpath(migrations_dir or MIGRATIONS_DIR)
    if not os.path.isdir(migrations_dir):
        return migrations
    for filename in os.listdir(migrations_dir):
        match = _MIGRATION_RE.match(filename)
        if match:
            version = int(match.group(1))
            module_name = filename[:-3]  # strip .py
            migrations.append((version, module_name, os.path.join(migrations_dir, filename)))
    migrations.sort(key=lambda m: (m[0], m[1]))
    return migrations


def _bridge_legacy(engine, migrations):
    """One-time: import already-applied history from schema_version into the
    filename ledger, then freeze the high-water mark with the sentinel.

    Under the old runner every on-disk file with version <= MAX(schema_version)
    was treated as applied (it either ran or is covered by the PG baseline stamp).
    Reproduce that exactly so nothing re-runs, then write the sentinel so this
    never repeats and a later-added lower-numbered file is NOT wrongly skipped.
    """
    with engine.begin() as conn:
        applied = _applied_names(conn)
        if _BASELINE_SENTINEL in applied:
            return  # already bridged
        legacy_max = _legacy_max_version(conn)
        for version, name, _ in migrations:
            if version <= legacy_max and name not in applied:
                _record_name(conn, name, version)
        _record_name(conn, _BASELINE_SENTINEL, legacy_max)
        logger.info("Migration ledger bridged from schema_version (baseline=%d)", legacy_max)


def run_migrations(engine, migrations_dir=None):
    """Discover and apply pending migrations, tracked by filename identity."""
    with engine.begin() as conn:
        _ensure_tables(conn)

    migrations = _discover_migrations(migrations_dir)
    _bridge_legacy(engine, migrations)

    with engine.begin() as conn:
        applied = _applied_names(conn)

    count = 0
    last = None
    for version, module_name, filepath in migrations:
        if module_name in applied:
            continue

        spec = importlib.util.spec_from_file_location(module_name, filepath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if not hasattr(mod, "up"):
            logger.warning("Migration %s has no up() function, skipping", module_name)
            continue

        with engine.begin() as conn:
            try:
                mod.up(conn)
                _record_name(conn, module_name, version)
                applied.add(module_name)
                count += 1
                last = module_name
                logger.info("Applied migration %s", module_name)
            except Exception:
                logger.exception("Migration %s FAILED -- rolling back", module_name)
                raise

    if count:
        logger.info("Migrations complete: %d applied (latest %s)", count, last)
    else:
        logger.info("Migrations: up to date (%d known)", len(migrations))
