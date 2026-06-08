"""Create rubric_changes -- the instrument-level change log.

A sibling to the per-song capture tables behind the Calibration Log
(pre_publish_corrections, song_recalibrations). This one tracks changes to the
rubric / calibration pipeline ITSELF -- tenets, procedural rules, the
contamination modifier, the schema version -- rather than to any one song's
reading. Rows are auto-detected at startup from tenets/core.json by
services/rubric_change_detector.py and surfaced as the 'rubric_change'
event_type in the unified feed.

change_slug ('R14@1.0') is the stable join key that
song_recalibrations.rubric_change_slug points at, so a rubric change can show
the songs it recalibrated.

PG-compatible (063+). Base.metadata.create_all() makes this on fresh installs
from the RubricChange model; this migration is the explicit idempotent path for
the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS rubric_changes (
            id SERIAL PRIMARY KEY,
            item_kind VARCHAR(20) NOT NULL,
            item_id VARCHAR(60) NOT NULL,
            item_version VARCHAR(20) NOT NULL,
            change_type VARCHAR(20) NOT NULL,
            tier_slug VARCHAR(20),
            title TEXT,
            before_text TEXT,
            after_text TEXT,
            ratified_at TIMESTAMP,
            schema_version VARCHAR(20),
            motion_id INTEGER REFERENCES motions(id) ON DELETE SET NULL,
            public_summary TEXT,
            internal_notes TEXT,
            change_slug VARCHAR(100) NOT NULL UNIQUE,
            detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            promoted_to_feed BOOLEAN NOT NULL DEFAULT TRUE,
            promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_rubric_changes_occurred "
        "ON rubric_changes(ratified_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_rubric_changes_item "
        "ON rubric_changes(item_id, item_version)"
    ))
