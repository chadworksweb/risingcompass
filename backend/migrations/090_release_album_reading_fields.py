"""Per-album reading fields: make a Release a first-class reading like a song.

Adds the album-level listener reading + Ether Art Chart entry, so an album
carries the same full reading surface a song does:
  - effects_prose  -- what the album does to a LISTENER (parallels song.effects_prose;
                      the album already had societal_prose, the society-scale read).
  - deadpan_line / topics / topic_audit -- album-level Ether Art Chart entry
                      (parallels a song's ether fields; topics JSON-encoded).

Existing album-level fields (charge_summary, arc_prose, societal_prose) shipped
in migration 069. PG-only; idempotent via IF NOT EXISTS. create_all() builds these
on fresh installs from the model.
"""

from sqlalchemy import text

_COLS = ["effects_prose", "deadpan_line", "topics", "topic_audit"]


def up(conn):
    for col in _COLS:
        conn.execute(text(f"ALTER TABLE releases ADD COLUMN IF NOT EXISTS {col} TEXT"))
