"""Rename the stream_songs table to cl_stream_songs.

CL Stream is one of the four "database songs" peer tables (the read-time union
exposed as the admin all_songs view). The table is renamed for clarity; the
Python model class stays StreamSong and the prose-provenance published label
stays "stream_songs" so existing on-chain anchors keep verifying
(see app/services/provenance_anchor.py).

ORDERING NOTE: main.py runs Base.metadata.create_all() BEFORE run_migrations().
On an existing DB the model now points at cl_stream_songs, so create_all will
have just created an EMPTY cl_stream_songs alongside the real, data-bearing
stream_songs. A plain RENAME would then fail ("relation cl_stream_songs already
exists"). So we drop that empty create_all artifact first, then rename the real
table into its place.

PG-compatible (063+). Idempotent across all three states:
  - Existing DB (stream_songs present): drop empty artifact, rename real table.
  - Fresh install (only cl_stream_songs present): no-op.
  - Already migrated (version guard): never re-runs.
"""

from sqlalchemy import text


def up(conn):
    has_old = conn.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = 'stream_songs'"
    )).fetchone()
    if not has_old:
        # Fresh install (create_all already made cl_stream_songs) or already
        # renamed by hand -- nothing to do.
        return

    # Drop the empty cl_stream_songs that create_all may have pre-created this
    # boot. Safe: if cl_stream_songs held the real data we would be in the
    # already-migrated state and this migration would not run at all.
    conn.execute(text("DROP TABLE IF EXISTS cl_stream_songs"))
    conn.execute(text("ALTER TABLE stream_songs RENAME TO cl_stream_songs"))
    conn.execute(text(
        "ALTER INDEX IF EXISTS idx_stream_songs_title_artist_ci "
        "RENAME TO idx_cl_stream_songs_title_artist_ci"
    ))
