"""Add chart_position_letter to compass_songs for double-A-side splits.

When a Billboard chart entry covers two distinct standalone songs packaged
on one physical single (a double-A-side - e.g., Toni Braxton's
"You're Makin' Me High / Let It Flow" at 1996 year-end #9), the songs are
calibrated as separate rows but share the same numeric chart_position.
The optional letter suffix ('A', 'B', etc.) distinguishes them.

NULL means a single song occupies the position (the common case).
Display logic renders as f"{chart_position}{chart_position_letter or ''}"
to produce "9A", "9B", or "10" depending on whether the letter is set.

Distinct from medleys: a medley is one continuous recording segueing
between songs, calibrated as a single row (e.g., 1969 #2 Aquarius/Let the
Sunshine In). Double-A-sides are two distinct recordings sharing a chart
slot - split into two rows with the letter suffix.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("ALTER TABLE compass_songs ADD COLUMN chart_position_letter TEXT"))
