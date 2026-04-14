"""Rename compass_songs.why_classification column to why_calibration.

Part of the classify → calibrate vocabulary refactor.

Idempotent: skips if the old column no longer exists (e.g. fresh install via
Base.metadata.create_all() which uses the new name).
"""

from sqlalchemy import text


def up(conn):
    cols = {row[1] for row in conn.execute(text("PRAGMA table_info(compass_songs)")).fetchall()}
    if "why_classification" in cols and "why_calibration" not in cols:
        conn.execute(text(
            "ALTER TABLE compass_songs RENAME COLUMN why_classification TO why_calibration"
        ))
