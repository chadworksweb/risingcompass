"""Public Participation Phase 3.1 -- account_verifications table.

One row per (user, provider) verification attempt. Plan calls for Tier 2
elevation (users.tier = 'id_verified') only after a verified row lands
here. Persona vs Stripe Identity decision: Stripe Identity (already in
the stack via Lyrical Charger donations -- one fewer vendor).

provider_reference is the Stripe VerificationSession id. status mirrors
Stripe's lifecycle: requires_input | processing | verified | canceled.
verified_at is null until the webhook confirms.

The user's tier flip is driven by the webhook, not the row insertion --
a created session is a not-yet-verified intent.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS account_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            provider_reference TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'requires_input',
            verified_at DATETIME,
            failure_reason TEXT,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE (provider, provider_reference)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_account_verifications_user "
        "ON account_verifications(user_id, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_account_verifications_status "
        "ON account_verifications(status, created_at DESC)"
    ))
