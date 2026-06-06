"""Drop the album-editorial (AlbumDeepDive) feature.

IRREVERSIBLE. Rising Compass holds zero editorial -- only calibration data.
The "album deep dive" feature (freeform `summary` + per-track `assessment`
prose, served at /api/albums) was the only editorial surface. This drops it.

NOT touched (albums-as-DATA, all stay):
  - releases / release_songs (Album Charger + MusicBrainz/Spotify artist albums)
  - weekly_album_readings / weekly_album_entries (Billboard 200 weekly chart)
  - album_calibrations (badge-serving aggregate layer)

`songs.album_id` was a nullable FK into album_deep_dives. The column is KEPT as a
plain nullable int (it is a generic pass-through used by song_sync / the backfill
engine / song_search / badge and carried no editorial); only its FK constraint to
album_deep_dives is dropped so the table can drop. All rows had album_id NULL.

The model classes AlbumDeepDive / AlbumTrack are removed in the same deploy so
create_all() cannot recreate the tables. PG-only; the runner wraps this in one
transaction. Guarded with to_regclass so a fresh create_all install (which never
builds album_deep_dives anymore) is a no-op.
"""

from sqlalchemy import text


def up(conn):
    # album_tracks FKs album_deep_dives -- drop it first.
    conn.execute(text("DROP TABLE IF EXISTS album_tracks"))

    # Drop the songs.album_id -> album_deep_dives FK constraint (keep the column),
    # then drop the editorial table. Skip entirely on a fresh install where the
    # table was never created.
    exists = conn.execute(text("SELECT to_regclass('album_deep_dives')")).scalar()
    if exists is not None:
        fks = conn.execute(text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "WHERE con.contype = 'f' AND rel.relname = 'songs' "
            "AND con.confrelid = to_regclass('album_deep_dives')"
        )).fetchall()
        for (name,) in fks:
            conn.execute(text('ALTER TABLE songs DROP CONSTRAINT IF EXISTS "%s"' % name))
        conn.execute(text("DROP TABLE album_deep_dives"))
