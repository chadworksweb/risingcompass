"""Ether Art Chart columns on submitted_songs + stream_songs.

The calibration path (calibrate_song_async) now returns ether tagging
(deadpan_line + topics + topic_audit) alongside the rubric + prose for every
in-road, not just the compass agent. compass_songs and library_songs already
carry these three columns; submitted_songs (Lyrical Charger) and stream_songs
did not, so a full calibration had nowhere to land its tags. Add them.

PG-compatible (063+). topics / topic_audit are JSON stored as text, matching
the compass_songs / library_songs columns the ether tagger already writes.
"""

from sqlalchemy import text


def up(conn):
    for table in ("submitted_songs", "stream_songs"):
        conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deadpan_line TEXT"
        ))
        conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS topics TEXT"
        ))
        conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS topic_audit TEXT"
        ))
