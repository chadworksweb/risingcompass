"""Drop compass_songs.why_calibration — legacy bootstrap-only column.

Investigated 2026-04-21b: "legacy column, populated only by pre-launch
extract/seed scripts. Not touched by live calibrator." Confirmed 2026-05-12
by the dead-code audit: only written by the pre-Turso bootstrap trio
(extract_songs.py / seed_db.py — also deleted in this pass) and read in
one admin-only JSON serializer behind an include_pii flag. No public
caller, no live writer, no analytical use.

SQLite supports DROP COLUMN since 3.35 (March 2021); libSQL (Turso)
inherits this. Falls back to a table recreate for older engines if needed.
"""

from sqlalchemy import text


def up(conn):
    cols = [r[1] for r in conn.execute(text("PRAGMA table_info(compass_songs)"))]
    if "why_calibration" in cols:
        conn.execute(text("ALTER TABLE compass_songs DROP COLUMN why_calibration"))
