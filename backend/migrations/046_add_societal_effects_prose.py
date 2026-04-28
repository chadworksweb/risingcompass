"""Per-song societal effects prose — paragraphs about what would happen if a
society ran this song's program at scale.

Parallel to effects_prose, but the unit of analysis is the collective rather
than the individual listener. The prose is grounded in the lyrics, the
calibration, and the Ether Art Chart fields (deadpan_line + topics) so it can
speak to "if millions manifest these topics/feelings/experiences, here is what
emerges socially and psychologically." Generated AFTER the ether tagger so
topics are available; rows without ether tags fall back to lyrics + calibration
only.

NULL means prose hasn't been generated yet. The frontend hides the section
when the column is NULL — there is no tier-generic fallback for societal
effects (a generic per-tier read would defeat the purpose).
"""

from sqlalchemy import text


def up(conn):
    for table in ("compass_songs", "library_songs", "submitted_songs", "stream_songs"):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN societal_effects_prose TEXT"))
