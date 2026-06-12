"""Add chart_snapshots.editorial -- the per-chart editorial summary, denormalised
onto every row of a (date, chart) snapshot at approval (equal across the day's
rows), so a standalone chart page renders the same charge + editorial + list +
ether shell the homepage daily reading shows.

The value reuses the editorial the compass agent ALREADY generates onto the
chart's AgentDraft (run_compass_agent -> _generate_editorial, kept current by the
lyrics-supply regen). Approval stamps draft.editorial_summary here; no new
Anthropic call at approval.

PG-compatible (063+). Base.metadata.create_all() builds the column on fresh
installs from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE chart_snapshots ADD COLUMN IF NOT EXISTS editorial TEXT"
    ))
