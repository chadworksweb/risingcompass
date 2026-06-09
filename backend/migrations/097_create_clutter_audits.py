"""Create clutter_audits -- the LEIT clutter-control human-audit queue.

One queue, two feeders: Lyrical Charger submissions that were warned as
non-commercial and pushed through anyway (source='lc_push'), and the daily LEIT
sweep agent's findings on songs that already slipped into the Library
(source='daily_sweep'). Flag-only -- a row here never mutates the live site; an
admin resolves each one (keep / remove / dismiss).

Tagged `environment` (local|prod) like the Faultline tables because local dev
shares the prod DB through the tunnel -- the admin queue filters by env so local
test rows stay out of the prod worklist.

PG-compatible (063+). Base.metadata.create_all() picks this up on fresh installs
from the model; this migration is the idempotent path for prod.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS clutter_audits (
            id SERIAL PRIMARY KEY,
            song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
            source VARCHAR(16) NOT NULL,
            category VARCHAR(24) NOT NULL,
            suggested_action VARCHAR(40),
            reason TEXT,
            confidence DOUBLE PRECISION,
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            environment VARCHAR(10) NOT NULL DEFAULT 'local',
            payload_json TEXT,
            detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            reviewed_by VARCHAR(120),
            review_notes TEXT
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_clutter_env_status "
        "ON clutter_audits(environment, status)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_clutter_song "
        "ON clutter_audits(song_id)"
    ))
    # At most one OPEN finding per song -- a re-sweep or repeat push won't stack
    # duplicate open rows. Resolved rows are exempt so history accumulates.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_clutter_open_song "
        "ON clutter_audits(song_id) "
        "WHERE status = 'open' AND song_id IS NOT NULL"
    ))
