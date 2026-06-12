"""Add the `preorder` disposition -- a sibling to `instrumental`, but TEMPORARY.

A chart feeder (especially iTunes Downloads) can list a single that is charting
on PRE-ORDER sales: it has no lyrics yet because it is not released. Unlike an
instrumental (permanently resolved, a cache hit forever), a preorder is a
placeholder: lyrics WILL exist on release, and the song must get a real reading
then. So preorder keeps NULL rubric_color/charge, is exempt from the approval
gate, is excluded from every aggregate, and is NOT a cache hit -- it re-lists as
awaiting-lyrics on each later day until the real lyrics drop (self-healing).

Three carriers:
- `songs.preorder`      -- sibling tag for parity with instrumental/translated/medley.
- `agent_draft_songs.preorder` -- the draft-song flag the approval gate reads.
- `chart_snapshots.preorder`   -- the published row's flag, for the panel label.

PG-compatible (063+). Base.metadata.create_all() builds the columns on fresh
installs from the models in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS preorder BOOLEAN NOT NULL DEFAULT false"
    ))
    conn.execute(text(
        "ALTER TABLE agent_draft_songs ADD COLUMN IF NOT EXISTS preorder BOOLEAN NOT NULL DEFAULT false"
    ))
    conn.execute(text(
        "ALTER TABLE chart_snapshots ADD COLUMN IF NOT EXISTS preorder BOOLEAN NOT NULL DEFAULT false"
    ))
