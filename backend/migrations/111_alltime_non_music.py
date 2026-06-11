"""Add non_music to the all-time chart tables.

kworb's all-time stream board includes non-music audio (white-noise / sleep /
ASMR tracks). Like an instrumental, a non-music entry carries NO charge -- but
it is not a song to be read at all, so it gets its own flag + tag instead of an
"awaiting lyrics" state. Nulled the same way an instrumental is, tagged
"non-music" in the UI.

PG-compatible (063+). create_all() handles fresh installs; this is the
idempotent prod path for the column add on existing tables.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE alltime_stream_songs "
        "ADD COLUMN IF NOT EXISTS non_music BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    conn.execute(text(
        "ALTER TABLE alltime_albums "
        "ADD COLUMN IF NOT EXISTS non_music BOOLEAN NOT NULL DEFAULT FALSE"
    ))
