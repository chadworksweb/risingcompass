"""Phase 2 of song identity resolution: merge-candidate queue + song-merge audit
+ the pg_trgm fuzzy rung's index, and backfill the clean-key collisions found by
migration 122 into the candidate queue.

Three tables:
- `song_merge_candidates` -- the human-audit queue (mirrors `clutter_audits`):
  one OPEN row per unordered (song_a, song_b) pair, env-tagged, resolved
  merge | keep_separate | dismiss. Candidates come from migration 122's clean-key
  collisions (backfilled here), the resolve ladder's gray band (Rung 3), and
  manual admin entry.
- `song_merge_events` -- the permanent merge audit log (mirrors
  `artist_admin_events`): one row per applied merge, with the rewrites breakdown.

The pg_trgm extension + a GIN trigram index on `songs.canonical_key_clean` power
Rung 3 (fuzzy fallback). Both are created inside SAVEPOINTs and FAIL-SOFT: DO
Managed Postgres normally allows `CREATE EXTENSION pg_trgm`, but if the role
lacks the privilege the migration must NOT brick startup -- the trgm rung ships
DARK behind a flag anyway, so it simply stays off and the merge queue still works.

Backfill: migration 122 logged clean-key collision groups. Here we materialize
them as OPEN candidates (reason='clean_collision') so they show up in the queue
immediately -- e.g. the 2026-06-13 pair (ids 2778/3293) becomes a one-click merge.

PG-compatible (063+). Base.metadata.create_all() builds the tables on fresh
installs from the models in app/models.py.
"""

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def up(conn):
    # --- merge-candidate queue (mirrors clutter_audits) --------------------- #
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_merge_candidates (
            id SERIAL PRIMARY KEY,
            source_song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
            target_song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
            reason VARCHAR(24) NOT NULL,
            confidence DOUBLE PRECISION,
            detected_by VARCHAR(24) NOT NULL DEFAULT 'backfill',
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
        "CREATE INDEX IF NOT EXISTS idx_merge_cand_env_status "
        "ON song_merge_candidates(environment, status)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_merge_cand_source ON song_merge_candidates(source_song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_merge_cand_target ON song_merge_candidates(target_song_id)"
    ))
    # One OPEN candidate per unordered pair. The pair is stored canonically
    # (source_song_id < target_song_id) so this partial unique dedups it; merge
    # DIRECTION is chosen by the admin at resolve time, not by column order.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_merge_cand_open_pair "
        "ON song_merge_candidates(source_song_id, target_song_id) "
        "WHERE status = 'open'"
    ))

    # --- merge audit log (mirrors artist_admin_events) --------------------- #
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_merge_events (
            id SERIAL PRIMARY KEY,
            occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actor VARCHAR(120),
            source_song_id INTEGER,
            source_title TEXT,
            source_artist TEXT,
            target_song_id INTEGER,
            target_title TEXT,
            target_artist TEXT,
            rewrites_json TEXT,
            notes TEXT,
            environment VARCHAR(10) NOT NULL DEFAULT 'local'
        )
    """))

    # --- backfill the clean-key collisions as OPEN candidates -------------- #
    from app.config import settings
    env = getattr(settings, "environment", "prod")
    conn.execute(text("""
        INSERT INTO song_merge_candidates
            (source_song_id, target_song_id, reason, confidence, detected_by, status, environment)
        SELECT a.id, b.id, 'clean_collision', 1.0, 'backfill', 'open', :env
        FROM songs a
        JOIN songs b
          ON a.canonical_key_clean = b.canonical_key_clean
         AND a.id < b.id
        WHERE a.canonical_key_clean IS NOT NULL
          AND a.canonical_key_clean <> ''
          AND a.canonical_key <> b.canonical_key
        ON CONFLICT DO NOTHING
    """), {"env": env})
    n = conn.execute(text(
        "SELECT count(*) FROM song_merge_candidates WHERE reason = 'clean_collision'"
    )).scalar()
    logger.info("migration 123: %s clean-collision merge candidate(s) queued (env=%s)", n, env)

    # --- pg_trgm extension + GIN index (FAIL-SOFT, trgm rung ships dark) ---- #
    try:
        with conn.begin_nested():
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        with conn.begin_nested():
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_songs_canonical_key_clean_trgm "
                "ON songs USING gin (canonical_key_clean gin_trgm_ops)"
            ))
        logger.info("migration 123: pg_trgm extension + trigram index ready")
    except SQLAlchemyError:
        logger.warning(
            "migration 123: pg_trgm unavailable -- trgm rung stays dark, merge "
            "queue still functional", exc_info=True,
        )
