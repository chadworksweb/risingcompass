"""Chart anomaly annotations table.

Backs the admin-authored markers on the daily chart (e.g. major album
releases that produce visible spikes). PG-compatible (063+).
Base.metadata.create_all() makes this on fresh installs from the
ChartAnomaly model; this migration is the explicit, idempotent path for
the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS chart_anomalies (
            id SERIAL PRIMARY KEY,
            date DATE NOT NULL,
            anomaly_type VARCHAR(40) NOT NULL DEFAULT 'album_release',
            artist TEXT,
            album TEXT,
            note TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_chart_anomalies_date "
        "ON chart_anomalies(date)"
    ))
