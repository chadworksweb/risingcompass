"""Create the Faultline error-ledger tables: error_signatures,
error_occurrences, error_actions.

Faultline is RC's internal reliability subsystem -- it captures every runtime
exception (via a logging.Handler, decoupled from app logic), deduplicates by a
normalized code fingerprint, and drives each distinct fault through a fix
lifecycle that a plugged-in agent or a human can run. Walled from Dev Ledger
(public/manual), Status Page (external uptime), lc_events (LC telemetry), and
api_call_log (HTTP-level). The ONLY out-reference is the nullable
dev_ledger_item_id -- the one-way "promote a confirmed fault to a public bug"
seam.

PG-compatible (063+). Base.metadata.create_all() picks these up on fresh
installs from the models; this migration is the idempotent path for prod.

See RISING-COMPASS-FAULTLINE-SCOPE.md.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS error_signatures (
            id SERIAL PRIMARY KEY,
            fingerprint VARCHAR(64) NOT NULL UNIQUE,
            exc_type VARCHAR(120),
            title TEXT NOT NULL,
            component VARCHAR(160),
            route TEXT,
            severity VARCHAR(10) NOT NULL DEFAULT 'medium',
            area VARCHAR(40),
            status VARCHAR(20) NOT NULL DEFAULT 'new',
            environment VARCHAR(10) NOT NULL DEFAULT 'local',
            first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            occurrence_count INTEGER NOT NULL DEFAULT 0,
            last_traceback TEXT,
            last_context TEXT,
            assigned_to VARCHAR(120),
            claimed_by VARCHAR(120),
            claim_expires_at TIMESTAMP,
            dev_ledger_item_id INTEGER REFERENCES dev_ledger_items(id) ON DELETE SET NULL,
            resolution TEXT,
            resolved_at TIMESTAMP,
            resolved_by VARCHAR(120),
            muted BOOLEAN NOT NULL DEFAULT FALSE
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_error_sig_status_sev_seen "
        "ON error_signatures(status, severity, last_seen_at)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_error_sig_env_status "
        "ON error_signatures(environment, status)"
    ))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS error_occurrences (
            id SERIAL PRIMARY KEY,
            signature_id INTEGER NOT NULL REFERENCES error_signatures(id) ON DELETE CASCADE,
            occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            traceback TEXT,
            context TEXT,
            environment VARCHAR(10) NOT NULL DEFAULT 'local'
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_error_occ_sig_time "
        "ON error_occurrences(signature_id, occurred_at)"
    ))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS error_actions (
            id SERIAL PRIMARY KEY,
            signature_id INTEGER NOT NULL REFERENCES error_signatures(id) ON DELETE CASCADE,
            action_type VARCHAR(24) NOT NULL,
            actor_type VARCHAR(10) NOT NULL,
            actor_ref VARCHAR(120),
            from_status VARCHAR(20),
            to_status VARCHAR(20),
            note TEXT,
            payload TEXT,
            idempotency_key VARCHAR(80),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_error_action_sig_time "
        "ON error_actions(signature_id, created_at)"
    ))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_error_action_idem "
        "ON error_actions(signature_id, idempotency_key)"
    ))
