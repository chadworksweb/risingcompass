"""Unified song-entity renovation -- Phase 5c-2 (rename + legacy poly drop).

Collapses the polymorphic song pointer to a single `song_id` FK on songs(id).

For the 14 colliding poly tables (they carry BOTH the legacy (song_source,
song_id) pair AND the unified_song_id FK added in migration 082), per table:
  1. Backfill any straggler unified_song_id so the drop never loses a pointer:
       (a) native rows -- song_source='songs' stored the unified id in the
           legacy song_id column, but some writers (e.g. user_calibrations)
           never set unified_song_id;
       (b) legacy-keyed rows still resolvable through song_id_map.
  2. DROP COLUMN song_source, DROP COLUMN legacy song_id (this auto-drops the
     old composite UNIQUE on (song_source, song_id, *extra) -- index or
     constraint, Postgres removes whichever depends on the columns).
  3. RENAME unified_song_id -> song_id (claims the freed name).

For comments + backfill_job_rows the legacy columns are differently named
(target_*/result_*), so there is NO collision: RENAME unified_song_id ->
song_id only. Their legacy columns drop in Phase 5d.

The 086 UNIQUE constraints (keyed on unified_song_id) auto-follow the rename to
song_id; the old composite uniques drop with their columns. The 4 hard-FK
tables (reading_songs, agent_draft_songs, lc_events, pre_publish_corrections)
already carry song_id -- untouched here; their legacy cols drop in 5d.

Idempotent: each per-table block is guarded on unified_song_id still existing,
so a re-application is a no-op (and never drops the renamed song_id). PG-only;
the runner wraps the whole migration in one transaction. FROZEN -- never
touched: prose_provenance_anchors.
"""

from sqlalchemy import text

# The 14 tables where unified_song_id collides with a legacy `song_id` column.
_COLLIDING = [
    "user_calibrations",
    "song_artists",
    "release_songs",
    "song_slugs",
    "calibration_runs",
    "audience_vibe_needles",
    "audience_vibe_pushes",
    "audience_vibe_review_cases",
    "misread_submissions",
    "song_recalibrations",
    "song_recalibration_proposals",
    "song_resets",
    "artist_verification_blocks",
    "artist_verification_inquiries",
]

# Legacy cols are target_*/result_* here -- no name clash, rename only.
_RENAME_ONLY = ["comments", "backfill_job_rows"]


def _has_col(conn, table, col):
    return conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": col}).scalar() is not None


def up(conn):
    for table in _COLLIDING:
        if not _has_col(conn, table, "unified_song_id"):
            continue  # already migrated -- never touch the renamed song_id
        # 1a. native rows: lift the unified id out of the legacy song_id column.
        conn.execute(text(
            f"UPDATE {table} SET unified_song_id = song_id "
            f"WHERE unified_song_id IS NULL AND song_source = 'songs'"
        ))
        # 1b. legacy-keyed stragglers still resolvable through the id map.
        conn.execute(text(
            f"UPDATE {table} t SET unified_song_id = m.new_song_id "
            f"FROM song_id_map m "
            f"WHERE t.unified_song_id IS NULL "
            f"  AND t.song_source = m.old_source AND t.song_id = m.old_id"
        ))
        # 2. drop the legacy polymorphic columns (auto-drops their old UNIQUE).
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS song_source"))
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS song_id"))
        # 3. claim the freed name.
        conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN unified_song_id TO song_id"))

    for table in _RENAME_ONLY:
        if _has_col(conn, table, "unified_song_id"):
            conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN unified_song_id TO song_id"))
