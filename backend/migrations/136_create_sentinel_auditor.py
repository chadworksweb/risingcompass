"""Sentinel Auditor Team -- enrollment funnel + findings ledger (ships DARK).

A bug-bounty-style red-team program. Vetted outsiders apply to become Sentinel
Auditors, dig through the platform, and file findings (inconsistencies, algorithm/
methodology holes, data errors, suggestions). Chad triages each finding. Auditors
earn reputation (no credits, no money). The whole public surface stays dark behind
the fail-closed system_flags key `sentinel_auditor.enabled` until launch; the admin
triage side is usable while dark.

Two tables:

  - sentinel_auditors -- one row per applying user (apply-once via UNIQUE user_id),
    status pending|approved|rejected|revoked. Mirrors the artist_verifications
    funnel: an admin reviews and approves/rejects.
  - sentinel_findings -- one row per submitted finding. scope='song' (FK songs)
    or 'general' (category enum), an auditor-proposed severity the admin can
    override, a faultline-style status lifecycle, and a point-in-time
    points_awarded snapshot stamped at acceptance (reputation is DERIVED by
    summing accepted points, not a running counter). Tagged `environment` like
    clutter_audits / faultline because local dev shares the prod DB via the
    tunnel -- the admin queue MUST filter by env so local test rows don't pollute
    the prod worklist.

PG-compatible (063+). Base.metadata.create_all() builds these on fresh installs
from the models in app/models.py; this creates them on existing DBs. Idempotent
(CREATE TABLE / INDEX IF NOT EXISTS).
"""

from sqlalchemy import text


def up(conn):
    # --- enrollment funnel ---------------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS sentinel_auditors (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            status          VARCHAR(16) NOT NULL DEFAULT 'pending',
            motivation      TEXT NOT NULL,
            focus_area      VARCHAR(24) NOT NULL,
            handle_snapshot VARCHAR(120),
            review_notes    TEXT,
            reviewed_by     VARCHAR(120),
            reviewed_at     TIMESTAMP,
            applied_at      TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
            updated_at      TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))
    # Application queue scans by status (pending = the review worklist).
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_sentinel_auditors_status "
        "ON sentinel_auditors (status)"
    ))

    # --- findings + triage ledger -------------------------------------------
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS sentinel_findings (
            id                SERIAL PRIMARY KEY,
            auditor_id        INTEGER NOT NULL REFERENCES sentinel_auditors(id) ON DELETE CASCADE,
            song_id           INTEGER REFERENCES songs(id) ON DELETE SET NULL,
            scope             VARCHAR(8) NOT NULL,
            category          VARCHAR(16) NOT NULL,
            title             VARCHAR(200) NOT NULL,
            description       TEXT NOT NULL,
            evidence_url      TEXT,
            proposed_severity VARCHAR(10) NOT NULL,
            accepted_severity VARCHAR(10),
            status            VARCHAR(16) NOT NULL DEFAULT 'new',
            disposition       TEXT,
            points_awarded    INTEGER NOT NULL DEFAULT 0,
            environment       VARCHAR(10) NOT NULL DEFAULT 'local',
            created_at        TIMESTAMP NOT NULL DEFAULT (now() at time zone 'utc'),
            updated_at        TIMESTAMP DEFAULT (now() at time zone 'utc'),
            reviewed_by       VARCHAR(120),
            reviewed_at       TIMESTAMP
        )
    """))
    # An auditor's own findings list (portal).
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_sentinel_findings_auditor "
        "ON sentinel_findings (auditor_id)"
    ))
    # The triage worklist filters by status.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_sentinel_findings_status "
        "ON sentinel_findings (status)"
    ))
    # The admin queue filters env first, then status (local dev shares prod DB).
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_sentinel_findings_env_status "
        "ON sentinel_findings (environment, status)"
    ))
    # Song-scoped findings surface on a per-song view.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_sentinel_findings_song "
        "ON sentinel_findings (song_id)"
    ))
