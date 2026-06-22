"""Sentinel Auditor Team -- notify-me waitlist.

Emails captured on the landing page while applications are closed (the dark
state). Single-step capture, no double opt-in, mirrors `lyrical_charger_subscribers`
(the LC-outage notice list): a manual admin dispatch emails everyone unnotified
when intake opens and stamps `notified_at`. Isolated from `rc_subscribers` (the
general subscriber funnel) -- different consent purpose.

PG-compatible; Base.metadata.create_all() builds it on fresh installs from the
model in app/models.py; this creates it on existing DBs. Idempotent.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS sentinel_waitlist (
            id          SERIAL PRIMARY KEY,
            email       TEXT NOT NULL UNIQUE,
            created_at  TIMESTAMP DEFAULT (now() at time zone 'utc'),
            notified_at TIMESTAMP
        )
    """))
