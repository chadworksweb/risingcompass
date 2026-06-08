"""Add chart_snapshots.compass_degree + charge_level -- per-day aggregate.

The chart-agnostic Calendar paints each day its spectrum color from that day's
aggregate compass_degree. The daily reading has that on daily_readings; chart
snapshots (iTunes Download Chart, etc.) did not -- the aggregate was only ever
computed live at render time and never stored, so a calendar of historical
chart days had nothing to color by. These two columns store the aggregate
(stamped on every row of a (date, chart) snapshot at approval, equal across the
day's rows). Nullable: fetch-time/unpublished rows carry none until approval.

PG-compatible (063+). Base.metadata.create_all() picks the columns up on fresh
installs from the updated ChartSnapshot model; this migration is the idempotent
path for the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE chart_snapshots ADD COLUMN IF NOT EXISTS compass_degree DOUBLE PRECISION"
    ))
    conn.execute(text(
        "ALTER TABLE chart_snapshots ADD COLUMN IF NOT EXISTS charge_level TEXT"
    ))
