"""Add the lyrics_unavailable disposition columns.

A RELEASED song whose lyrics are genuinely unobtainable (not published on any
source) used to re-list as awaiting-lyrics on every feeder run, with no honest
way to settle it: it is not instrumental (it HAS lyrics), not a pre-order (it is
released), and inventing a tier would be a lie. lyrics_unavailable is the
disposition for it -- a permanent NULL-tier cache hit that stops the daily
re-list, exempts the draft from the approval gate, and stays out of every
aggregate. Cleared automatically if real lyrics later surface and supersede it.

- songs.lyrics_unavailable          -- the persistent Library state (cache hit)
- agent_draft_songs.lyrics_unavailable -- the per-draft mark (gate exemption)

Both default false. Idempotent; PG-compatible (063+). Base.metadata.create_all()
builds the columns on fresh installs from the models.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS lyrics_unavailable "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    conn.execute(text(
        "ALTER TABLE agent_draft_songs ADD COLUMN IF NOT EXISTS lyrics_unavailable "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    ))
