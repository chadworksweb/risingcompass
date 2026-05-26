"""Async job table for the Album Charger.

Album charging is minutes of sequential Opus work, so the submit endpoint
records a job here and returns a token; a background task processes it and the
frontend polls. PG-compatible (063+). Base.metadata.create_all() makes this on
fresh installs from the AlbumChargeJob model; this is the explicit, idempotent
path for the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS album_charge_jobs (
            id SERIAL PRIMARY KEY,
            job_token VARCHAR(64) NOT NULL UNIQUE,
            status VARCHAR(20) NOT NULL DEFAULT 'queued',
            phase VARCHAR(30),
            total_tracks INTEGER DEFAULT 0,
            calibrated_tracks INTEGER DEFAULT 0,
            result_json TEXT,
            error_message TEXT,
            ip_address VARCHAR(45),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_album_charge_jobs_created "
        "ON album_charge_jobs(created_at DESC)"
    ))
