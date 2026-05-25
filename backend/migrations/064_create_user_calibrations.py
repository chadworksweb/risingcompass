"""Lyrical Charger -- per-user "songs you've calibrated" table.

One row per (user, canonical song). A signed-in user running a song through
Lyrical Charger upserts here; the account page reads it back as "Songs you've
calibrated." Anonymous runs are not attributed.

PG-compatible (064+). Base.metadata.create_all() already creates this table
on fresh installs from the UserCalibration model; this migration is the
explicit, idempotent path for the existing prod DB (and guarantees the
indexes / unique constraint exist regardless of create_all ordering).

user_id carries no FK on purpose -- it mirrors audience_vibe_pushes. The row
is written from a write session distinct from the Clerk session that
provisions the user, so a hard FK would risk a cross-transaction race.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS user_calibrations (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            song_slug TEXT,
            title TEXT,
            artist TEXT,
            rubric_color TEXT,
            charge_value INTEGER,
            charge_summary TEXT,
            calibrated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_calibration_song "
        "ON user_calibrations(user_id, song_source, song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_user_calibrations_user "
        "ON user_calibrations(user_id, calibrated_at DESC)"
    ))
