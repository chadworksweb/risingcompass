"""Unified song-entity renovation -- Phase 5d (drop the legacy song tables).

IRREVERSIBLE. The four legacy song tables (compass_songs, library_songs,
submitted_songs, cl_stream_songs) were folded into the unified `songs` table in
Phase 2 and have been write-frozen + read-dark since the 5b cutover. This drops
them and the three remaining FK columns that point into them.

Steps:
  1. Recover recent lc_events song linkage: post-5b, lc_events.submission_id was
     written with the unified songs.id (the store_calibrated_song return),
     mislabeled. Copy those into lc_events.song_id (the real unified FK, added in
     082) where the value is a live songs.id, before dropping submission_id.
  2. Drop the FK columns that reference the legacy tables (releases the FK so the
     tables can drop): reading_songs.compass_song_id, agent_draft_songs
     .compass_song_id, lc_events.submission_id.
  3. Drop the four legacy tables.

NOT touched here:
  - pre_publish_corrections.compass_song_id -- it has NO FK to compass_songs
    (plain int) and now holds unified songs.id; it stays as a live (if stale-
    named) pointer. Renaming it is deferred.
  - comments.target_* / backfill_job_rows.result_* -- still in use (artist/
    release comment targets; backfill bookkeeping). Kept.
  - prose_provenance_anchors -- FROZEN; its song_table labels are TEXT, not FKs,
    so dropping the tables does not touch it.

The model classes CompassSong/LibrarySong/SubmittedSong/StreamSong are removed in
the same deploy so create_all() cannot recreate the tables. PG-only; the runner
wraps this in one transaction. Plain DROP TABLE (no CASCADE) so any unexpected
remaining dependency surfaces as an error and rolls the whole migration back.
"""

from sqlalchemy import text

_LEGACY_TABLES = ["compass_songs", "library_songs", "submitted_songs", "cl_stream_songs"]


def up(conn):
    # 1. Recover recent lc_events linkage before dropping submission_id.
    conn.execute(text(
        "UPDATE lc_events SET song_id = submission_id "
        "WHERE song_id IS NULL AND submission_id IN (SELECT id FROM songs)"
    ))

    # 2. Drop the FK columns pointing into the legacy tables.
    conn.execute(text("ALTER TABLE reading_songs DROP COLUMN IF EXISTS compass_song_id"))
    conn.execute(text("ALTER TABLE agent_draft_songs DROP COLUMN IF EXISTS compass_song_id"))
    conn.execute(text("ALTER TABLE lc_events DROP COLUMN IF EXISTS submission_id"))

    # 3. Drop the legacy song tables.
    for t in _LEGACY_TABLES:
        conn.execute(text("DROP TABLE IF EXISTS %s" % t))
