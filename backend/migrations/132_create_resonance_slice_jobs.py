"""Audience Resonance -- durable async slice jobs.

The slice flow (POST /slice -> poll /slice/{token} -> reveal, then /submit
persists the server-computed verdict by token) was backed by an in-process
registry. That does not survive a worker restart and breaks under multiple
uvicorn workers: a token minted on one worker is invisible to another, so
/submit cannot resolve the verdict and falls back to neutral. This table makes
the job + its computed slice durable and cross-worker, mirroring
album_charge_jobs. PG-compatible, idempotent.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS resonance_slice_jobs (
            id            SERIAL PRIMARY KEY,
            job_token     VARCHAR(64) NOT NULL UNIQUE,
            status        VARCHAR(20) NOT NULL DEFAULT 'queued',
            slice_json    TEXT,
            error_message TEXT,
            created_at    TIMESTAMP DEFAULT (now() at time zone 'utc'),
            updated_at    TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))
