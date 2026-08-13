"""Per-artist release suppressions: curated exclusions that survive a rebuild.

The codified filter (RISING-COMPASS-ARTIST-RELEASES.md) drops releases by
secondary type, edition title, hits markers and the bootleg gate. What it cannot
drop is a release that MusicBrainz files as an OFFICIAL release of a valid type,
credited to the artist, which nonetheless is not that artist's catalogue. The
Beatles carry a whole family of these: the 1961 Tony Sheridan / Beat Brothers
Hamburg sessions (My Bonnie, Cry for a Shadow, Ain't She Sweet, Sweet Georgia
Brown), the fan-club Christmas flexi discs, and a 1978 novelty.

Those were deleted by hand on 2026-08-13 -- and a hand delete does not survive.
rebuild-releases re-fetches the catalogue from MusicBrainz, so the very next
rebuild silently re-created every one of them. This table makes the curation
durable: the resolve consults it and never writes a suppressed release again.

Matched on the NORMALISED TITLE rather than the release-group MBID, deliberately:

  * MBIDs churn. A group that gets merged or re-filed in MusicBrainz comes back
    under a new id and slips straight past an MBID-keyed list.
  * The curator's judgement is about the record, not the identifier. "My Bonnie
    is not a Beatles release" stays true however MB files it.
  * The delete that motivated this table was itself title-curated.

Normalisation folds the curly apostrophe (U+2019) to ASCII and lowercases, because
MusicBrainz titles carry curly quotes and a straight-quote comparison silently
misses "Ain't She Sweet" -- which it did on the first pass at this cleanup.

Scoped per artist: suppressing "Help!" for one artist must never touch another's.
PG-compatible; create_all() builds it from the ReleaseSuppression model on fresh
installs.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS release_suppressions (
            id SERIAL PRIMARY KEY,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            title_norm TEXT NOT NULL,
            title_snapshot TEXT NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    # One suppression per title per artist; re-suppressing is a no-op upsert.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_release_suppression_artist_title "
        "ON release_suppressions (artist_id, title_norm)"
    ))
    # The resolve loads an artist's whole suppression set once per pass.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_release_suppressions_artist "
        "ON release_suppressions (artist_id)"
    ))
