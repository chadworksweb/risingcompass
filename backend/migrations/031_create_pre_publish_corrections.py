"""Pre-publish corrections — audit log for admin overrides of agent-classified
draft songs during daily-reading review, before the draft is approved.

Until now, corrections during daily-reading review were applied via direct SQL
UPDATE on compass_songs + agent_draft_songs. No record that the override happened,
no before/after snapshot, no reasoning captured. This table is the audit home
for those events — the first capture table in the Calibration Log system.

Each row captures a single admin correction: the draft song it targeted, the
before_* snapshot of every field that could change (rubric_color, charge_value,
contaminated, contamination_note, summary), the after_* values, and an optional
human_rationale the admin may supply at correction time or later when promoting.

promoted_to_feed starts false. A separate promote step flips it to true and
stamps promoted_at — that's the deliberate editorial act that puts the event
in the public Calibration Log feed.

See RISING-COMPASS-CALIBRATION-LOG.md for the broader architecture.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS pre_publish_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER,
            draft_song_id INTEGER,
            compass_song_id INTEGER,
            occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            before_rubric_color TEXT,
            before_charge_value INTEGER,
            before_contaminated BOOLEAN,
            before_contamination_note TEXT,
            before_summary TEXT,
            after_rubric_color TEXT,
            after_charge_value INTEGER,
            after_contaminated BOOLEAN,
            after_contamination_note TEXT,
            after_summary TEXT,
            human_rationale TEXT,
            tags TEXT,
            promoted_to_feed BOOLEAN DEFAULT 0 NOT NULL,
            promoted_at DATETIME
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pre_publish_corrections_draft_song "
        "ON pre_publish_corrections(draft_song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pre_publish_corrections_compass_song "
        "ON pre_publish_corrections(compass_song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pre_publish_corrections_occurred_at "
        "ON pre_publish_corrections(occurred_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_pre_publish_corrections_promoted "
        "ON pre_publish_corrections(promoted_to_feed, promoted_at DESC)"
    ))
