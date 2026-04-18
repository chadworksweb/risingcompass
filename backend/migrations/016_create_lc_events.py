"""Create lc_events table for Lyrical Charger activity + event logging."""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS lc_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            event_type VARCHAR(40) NOT NULL,
            ip_address VARCHAR(64),
            user_agent VARCHAR(512),
            referrer VARCHAR(512),
            payload_json TEXT,
            submission_id INTEGER REFERENCES submitted_songs(id) ON DELETE SET NULL
        )
    """))

    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_lc_events_ts ON lc_events(occurred_at DESC)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_lc_events_type ON lc_events(event_type)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_lc_events_ip ON lc_events(ip_address)"))
