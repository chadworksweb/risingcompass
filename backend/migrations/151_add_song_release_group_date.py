"""Song cover art: persist the resolved release-group's first-release date.

The resolver already computes this (musicbrainz._pick_release_group returns
`first_release_date`, and uses it to rank candidates EARLIEST-first), but it was
discarded once a winner was chosen -- only the MBID and the checked-at marker were
stamped. That left no way to tell a good pick from a bad one after the fact
without re-querying MusicBrainz at 1 req/sec.

It needs to be tellable in bulk. The dominant wrong-art failure is not a wrong
ARTIST -- the artist-credit check in _pick_release_group holds -- it is a right
artist on the wrong RELEASE: an archival set, a reissue, or an unrelated later
single that MB never tagged as a compilation. Those are invisible to a title or
type heuristic, but they are obvious against the calendar: a song that charted in
1972 pointing at a release group first issued in 2022 is wrong, and saying so
needs no knowledge of the record itself.

Storing the date makes that audit a plain SQL join against chart_appearances
instead of a 20-minute re-lookup pass, so the check can run after every backfill
chunk rather than being a thing someone gets around to.

  release_group_date -- MB's first-release-date for the picked release group,
                        as MB reports it ("1972", "1972-02", "1972-02-01"), or
                        NULL when MB has no date. TEXT, not DATE: MB dates are
                        variable-precision and compare correctly as strings,
                        since a shorter prefix sorts before its own longer forms.

Populated ONLY by scripts/backfill_song_cover_art.py, alongside the two columns
migration 146 added. NULL on every row already resolved before this migration --
those predate the stamp and are re-derivable with --recheck-misses or a re-resolve,
not backfilled here (it would cost one throttled MB lookup per row).

create_all() builds this on fresh installs from the Song model; this migration is
the explicit, idempotent add for existing databases. PG-compatible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS release_group_date TEXT"
    ))
