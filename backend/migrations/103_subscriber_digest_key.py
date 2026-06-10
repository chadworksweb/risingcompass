"""Add rc_subscribers.last_digest_key -- per-recipient digest dedup (Build 2b
Phase 2). Stores the date key (YYYY-MM-DD) of the last reading digest sent to
this subscriber, so a re-run of the digest cron only targets recipients who have
not yet received the current reading. Cadence-agnostic: daily or weekly both
dedup correctly because the key is the reading's own date.

PG-compatible (063+).
"""
from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE rc_subscribers ADD COLUMN IF NOT EXISTS last_digest_key VARCHAR(10)"
    ))
