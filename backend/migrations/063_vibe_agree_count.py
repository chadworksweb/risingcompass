"""Audience Vibe — add the agree tally + switch to consensus math.

The needle no longer accumulates raw +/-1 steps. It is recomputed from the
vote tallies each push as a bounded, inertia-damped consensus:

    offset = round( W * (up - down) / (up + agree + down + k) )
    value  = clamp(compass + offset, -100, +100)

W = max swing from compass (15), k = inertia constant (8). The Agree vote
(direction 0) only grows the denominator, so it anchors the needle toward
the compass score instead of moving it.

PG-compatible (063+). Adds pushes_agree_total to the needle table.
direction already permits 0 (no CHECK constraint on the column).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE audience_vibe_needles "
        "ADD COLUMN IF NOT EXISTS pushes_agree_total INTEGER NOT NULL DEFAULT 0"
    ))
