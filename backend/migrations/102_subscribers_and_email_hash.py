"""Create rc_subscribers (the on-site email-subscriber layer) and add
users.email_hash (the no-duplicate link key).

This is Build 2b of the Hockey Stick plan: a lightweight email-capture layer
that is the top of RC's own subscriber funnel (NOT chadlewine). It is separate
from lyrical_charger_subscribers (which is the LC-outage notice list -- a
different consent purpose).

No-duplicate / promote-to-Clerk mechanism: users deliberately stores NO email
(Clerk holds it; the row is pseudonymous by handle). So we match a subscriber to
their future Clerk account by a sha256 hash of the normalized email -- stored on
both sides (rc_subscribers.email_hash always; users.email_hash populated at
provision via the Clerk backend API, fail-soft). A subscriber who creates an
account links by hash; an account holder who subscribes later links the same way.
The plaintext email lives only in rc_subscribers (a legitimate consent list) and
in Clerk -- never on users.

PG-compatible (063+). Base.metadata.create_all() picks up the model on fresh
installs; this migration is the idempotent path for prod.
"""

from sqlalchemy import text


def up(conn):
    # --- users.email_hash: the link key (hash only, never plaintext) ---
    conn.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_hash VARCHAR(64)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_users_email_hash ON users(email_hash)"
    ))

    # --- rc_subscribers: the on-site subscriber list ---
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS rc_subscribers (
            id SERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            email_hash VARCHAR(64) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            source VARCHAR(40),
            source_detail TEXT,
            confirm_token VARCHAR(64),
            confirmed_at TIMESTAMP,
            unsubscribe_token VARCHAR(64) NOT NULL,
            unsubscribed_at TIMESTAMP,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            promoted_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_rc_subscribers_email_hash "
        "ON rc_subscribers(email_hash)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_rc_subscribers_status "
        "ON rc_subscribers(status)"
    ))
    # confirm_token is consumed (nulled) on confirm, so a partial unique keeps
    # live tokens distinct without colliding on the many NULLs.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rc_subscribers_confirm_token "
        "ON rc_subscribers(confirm_token) WHERE confirm_token IS NOT NULL"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rc_subscribers_unsub_token "
        "ON rc_subscribers(unsubscribe_token)"
    ))
