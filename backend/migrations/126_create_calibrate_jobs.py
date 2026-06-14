"""Single-song calibrate jobs -- async job + poll for the public Lyrical Charger.

A public single-song charge is several sequential Opus calls (identity ->
calibrate -> listener -> ether -> societal -> save). Held open as one blocking
HTTP request, the tail read as a hang and the frontend bar could only fake
progress (it parked at a fixed % until the response landed). This table moves
the PUBLIC path to the same async job model the Album Charger already uses
(album_charge_jobs): POST /api/analyzer/calibrate-lyrics/start validates
synchronously, creates one of these rows, launches a background worker, and
returns a token; the frontend polls GET .../calibrate-lyrics/status/{token} and
drives a REAL progress bar from the worker's `phase`. The synchronous
/calibrate-lyrics endpoint stays exactly as-is for service/API callers, so their
contract is unchanged.

`phase` values: queued | identity | calibrating | listener | ether | societal |
saving | done. `result_json` holds the final LyricsCalibrateOut payload (every
terminal status -- scored, run_capped, not_commercial_warning, lyrics_mismatch,
lyrics_diverge_from_prior, saved_view_on_page, error -- is delivered through it,
so the poll surface mirrors the synchronous endpoint's return shapes).

PG-compatible (063+). Base.metadata.create_all() builds it on fresh installs
from the model in app/models.py; this creates it on existing DBs. Idempotent
(CREATE TABLE / INDEX IF NOT EXISTS).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS calibrate_jobs (
            id            SERIAL PRIMARY KEY,
            job_token     VARCHAR(64) NOT NULL UNIQUE,
            status        VARCHAR(20) NOT NULL DEFAULT 'queued',
            phase         VARCHAR(30),
            result_json   TEXT,
            error_message TEXT,
            ip_address    VARCHAR(45),
            created_at    TIMESTAMP DEFAULT (now() at time zone 'utc'),
            updated_at    TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))

    # Cleanup/observability reads scan by recency.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_calibrate_jobs_created_at "
        "ON calibrate_jobs (created_at)"
    ))
