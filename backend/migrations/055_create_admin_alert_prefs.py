"""Admin alert preferences -- per-alert toggle keyed by alert_key.

One row per (alert_key, channel) pair. Channel is currently always 'email'
but the column is here so SMS / Slack / webhook can be added without a
schema migration.

Seeded with the only alert currently wired: comment_created, enabled=true.
The user explicitly asked for an email on every new comment until traffic
warrants throttling.

Other alert_keys live in the UI as 'coming soon' placeholders -- they're
not written here until they have backend code that fires them, so
flipping their toggle does nothing yet.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_alert_prefs (
            alert_key TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'email',
            enabled BOOLEAN NOT NULL DEFAULT 1,
            updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (alert_key, channel)
        )
    """))
    # Seed the comment-created alert as enabled. INSERT OR IGNORE so a
    # re-run never clobbers an admin's later toggle.
    conn.execute(text(
        "INSERT OR IGNORE INTO admin_alert_prefs (alert_key, channel, enabled) "
        "VALUES ('comment_created', 'email', 1)"
    ))
