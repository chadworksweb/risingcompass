"""Rubric-update recalibrations — a third recalibration_type alongside
satire and public_interest.

When a rubric rule or tenet is added/changed and the change demotes or
promotes specific songs, those songs get a SongRecalibration entry of
type "rubric_update". Two new columns capture the rubric-level cause:

  - rubric_change_slug — stable identifier ("specific-group-anthem-ceiling")
    so multiple songs affected by the same rubric change group together
    in the audit log.
  - rubric_change_note — 1-2 sentence description of the rule and why
    it was added.

Also adds three columns to calibration_runs so prior runs under an old
rubric can be marked superseded. Consensus computation filters them out
so post-rubric-update runs aren't diluted by pre-update history.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE song_recalibrations ADD COLUMN rubric_change_slug VARCHAR(100)"
    ))
    conn.execute(text(
        "ALTER TABLE song_recalibrations ADD COLUMN rubric_change_note TEXT"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_recalibrations_rubric_slug "
        "ON song_recalibrations(rubric_change_slug)"
    ))

    conn.execute(text(
        "ALTER TABLE song_recalibration_proposals ADD COLUMN rubric_change_slug VARCHAR(100)"
    ))
    conn.execute(text(
        "ALTER TABLE song_recalibration_proposals ADD COLUMN rubric_change_note TEXT"
    ))

    conn.execute(text(
        "ALTER TABLE calibration_runs ADD COLUMN superseded BOOLEAN DEFAULT 0 NOT NULL"
    ))
    conn.execute(text(
        "ALTER TABLE calibration_runs ADD COLUMN superseded_reason VARCHAR(100)"
    ))
    conn.execute(text(
        "ALTER TABLE calibration_runs ADD COLUMN superseded_at DATETIME"
    ))
