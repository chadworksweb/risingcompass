"""Song-level cover art: resolve a release-GROUP MBID per song.

Cover art has been keyed to releases since migration 091 (mb_cover_art, keyed by
the stable release-group MBID). That only ever reached songs linked to a Release
-- chart-born and Lyrical-Charger-born singles have no ReleaseSong row at all, so
their song pages carried no art.

These two columns let a song point at its own release-group so it reads the SAME
mb_cover_art cache and the SAME derived CAA URLs. No new art source, no new legal
posture: Cover Art Archive stays the only one (Apple's iTunes Search API was
evaluated and REJECTED -- its Promo Content grant requires a "Download on iTunes"
badge proximate to every image and is written for pages that promote the content,
which a critical reading is not).

  release_group_mbid       -- the resolved MBID, or NULL
  release_group_checked_at -- when the resolve last ran, regardless of outcome

checked_at set + mbid NULL is a recorded MISS ("searched, no confident match"), so
the backfill skips the song instead of re-searching it forever. Both are populated
ONLY by scripts/backfill_song_cover_art.py -- MusicBrainz is 1 req/sec, far too
slow for the request path.

create_all() builds these on fresh installs from the Song model; this migration is
the explicit, idempotent add for existing databases. PG-compatible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS release_group_mbid TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS release_group_checked_at TIMESTAMP"
    ))
    # The backfill's work queue is "songs never checked", so index the checked
    # marker rather than the MBID. Partial: the rows it scans are exactly the
    # NULLs, and that set shrinks toward empty as the SOP runs.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_songs_release_group_unchecked "
        "ON songs (id) WHERE release_group_checked_at IS NULL"
    ))
