"""Per-song effects prose — 3-paragraph description of what the song
transmits and what it may do to a listener.

Replaces the tier-generic TIER_EFFECTS string on the song page with
Claude-generated prose anchored in each song's charge_summary. Stored
as a single TEXT column per song source table; paragraphs are split
on blank lines client-side.

Applies to all four song source tables so the Library preview, CL
Stream, LC submissions, and the compass chart archive all get prose.
NULL means prose hasn't been generated yet — the frontend falls back
to the tier-generic copy.
"""

from sqlalchemy import text


def up(conn):
    for table in ("compass_songs", "library_songs", "submitted_songs", "stream_songs"):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN effects_prose TEXT"))
