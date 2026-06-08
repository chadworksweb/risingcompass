"""Add chart_snapshots.published -- approval gate for chart-snapshot panels.

Chart snapshots (Viral 50, etc.) are written at fetch time, BEFORE the admin
has approved the draft and supplied lyrics. Without a publish gate the public
panel would serve a half-calibrated, unapproved chart the instant the scraper
ran. This column makes the chart behave like the daily reading: rows are
written unpublished, and chart-draft approval flips them to published. The
public endpoint only serves published rows.

PG-compatible (063+). Base.metadata.create_all() picks the column up on fresh
installs from the updated ChartSnapshot model; this migration is the idempotent
path for the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE chart_snapshots ADD COLUMN IF NOT EXISTS published "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    ))
