"""Extend song_recalibrations with Calibration Log promote columns.

Phase 2 of the Calibration Log (see RISING-COMPASS-CALIBRATION-LOG.md).
Brings every post-publish recalibration row — across all pipelines and
lenses — onto the same feed contract as pre_publish_corrections.

Three new columns:
  - human_rationale  — admin prose commentary on the recalibration,
    separate from the AI's ai_rationale. This is what the public feed
    renders when the event is promoted.
  - promoted_to_feed — default 0, flipped by POST .../promote.
  - promoted_at      — timestamp the flip happened.

Back-fill policy (Option A from the plan): every existing row sets
promoted_to_feed=1. Prior to this migration those rows were always
visible on public song pages; back-filling preserves existing public
behavior. Curation applies to NEW rows going forward.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE song_recalibrations ADD COLUMN human_rationale TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE song_recalibrations ADD COLUMN tags TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE song_recalibrations ADD COLUMN promoted_to_feed "
        "BOOLEAN DEFAULT 0 NOT NULL"
    ))
    conn.execute(text(
        "ALTER TABLE song_recalibrations ADD COLUMN promoted_at DATETIME"
    ))

    # Back-fill existing rows to promoted_to_feed=1 — preserves current
    # public behavior (every song_recalibrations row was always visible on
    # the public song page before this migration). promoted_at mirrors
    # applied_at so ordering stays honest.
    conn.execute(text(
        "UPDATE song_recalibrations "
        "SET promoted_to_feed = 1, promoted_at = applied_at "
        "WHERE promoted_to_feed = 0"
    ))

    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_recalibrations_promoted "
        "ON song_recalibrations(promoted_to_feed, promoted_at DESC)"
    ))
