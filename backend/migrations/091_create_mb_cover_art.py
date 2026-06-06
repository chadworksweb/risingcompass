"""Cover Art Archive lookup cache, keyed by MusicBrainz release-group MBID.

A standalone cache table -- deliberately NOT a column on `releases`, because
release rows are torn down and recreated whenever an artist's releases are
rebuilt (the PK churns). Keying on the stable release-group MBID means the
cache survives rebuilds and each group is fetched from CAA exactly once.

create_all() builds this on fresh installs from the MbCoverArt model; this
migration is the explicit, idempotent create for existing databases and bumps
the schema version. PG-compatible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS mb_cover_art ("
        "  musicbrainz_id TEXT PRIMARY KEY,"
        "  has_art BOOLEAN NOT NULL DEFAULT FALSE,"
        "  checked_at TIMESTAMP"
        ")"
    ))
