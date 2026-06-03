"""Unified song-entity renovation -- additive reference pointers (Phase 1).

Adds the new pointer to songs(id) ALONGSIDE the existing legacy pointers, so the
Phase 2 backfill can repoint every song reference while the OLD columns stay
populated and authoritative (clean rollback: revert code = working state).

Two naming conventions, both renamed to `song_id` in Phase 5 after the legacy
columns are dropped:
  - Tables with NO existing `song_id`  -> add `song_id`        (hard-FK collapse)
      reading_songs, agent_draft_songs, lc_events, pre_publish_corrections
  - Tables WITH a polymorphic `song_id` (+ song_source) -> add `unified_song_id`
      user_calibrations, song_artists, release_songs, song_slugs, calibration_runs,
      audience_vibe_needles, audience_vibe_pushes, audience_vibe_review_cases,
      misread_submissions, song_recalibrations, song_recalibration_proposals,
      song_resets, artist_verification_blocks, artist_verification_inquiries,
      backfill_job_rows, comments

FROZEN -- intentionally NOT touched: prose_provenance_anchors (Bitcoin-anchored
hash identity). PG-compatible; idempotent (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text

# Tables that gain a brand-new `song_id` FK (no name clash).
_HARD_FK_TABLES = [
    "reading_songs",
    "agent_draft_songs",
    "lc_events",
]

# Polymorphic tables already carrying (song_source, song_id): gain a separate
# `unified_song_id` FK so the legacy pair survives until Phase 5.
_POLY_TABLES = [
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
    "backfill_job_rows",
    "comments",
]


def up(conn):
    for t in _HARD_FK_TABLES:
        conn.execute(text(
            f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS song_id INTEGER "
            f"REFERENCES songs(id) ON DELETE SET NULL"
        ))
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS idx_{t}_song_id ON {t}(song_id)"
        ))

    # pre_publish_corrections.compass_song_id is a plain (non-FK) informational
    # pointer; mirror that -- add a plain song_id (no FK) to match.
    conn.execute(text(
        "ALTER TABLE pre_publish_corrections ADD COLUMN IF NOT EXISTS song_id INTEGER"
    ))

    for t in _POLY_TABLES:
        conn.execute(text(
            f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS unified_song_id INTEGER "
            f"REFERENCES songs(id) ON DELETE SET NULL"
        ))
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS idx_{t}_unified_song_id "
            f"ON {t}(unified_song_id)"
        ))
