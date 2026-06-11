"""Backfill remaining songs.created_at NULLs from societal_prose_generated_at.

Follow-on to migration 114. Songs ingested via the terminal catalog backfill
(e.g. the Michael Jackson album set) have no calibration_runs.run_at and no
song_ingestions.created_at, so 114's two backfill passes left them NULL. They DO
carry a real societal_prose_generated_at (the write-time prose seal -- the actual
time the backfill ran), which is the most accurate creation signal available.

Use it as a third fallback. Rows with no run, no ingestion, and no prose seal
(a couple of signal-less chart_reading edge rows) stay NULL -- there is no honest
timestamp to assign them. Idempotent (only touches NULLs). PG-compatible (063+).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "UPDATE songs SET created_at = societal_prose_generated_at "
        "WHERE created_at IS NULL AND societal_prose_generated_at IS NOT NULL"
    ))
