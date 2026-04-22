"""compass_songs.created_at — real timestamps for unified "all songs" sort.

Before: compass_songs had no created_at column, so the admin all_songs
tab's default "newest first" sort collapsed every compass row into
datetime(year, 1, 1) and shoved them behind rows with real timestamps.
Thousands of compass songs effectively hidden beyond page N.

After: every compass row has created_at. Existing rows backfill to
(year-01-01) offset by `id` seconds so same-year rows keep their
id-order tiebreak without colliding. New rows stamp datetime.utcnow()
via the SQLAlchemy default.
"""

from sqlalchemy import text


def up(conn):
    # SQLite ADD COLUMN doesn't allow non-constant defaults, so add
    # the column nullable first, backfill, then leave NULL allowed
    # (the ORM default handles new inserts).
    conn.execute(text("ALTER TABLE compass_songs ADD COLUMN created_at DATETIME"))
    conn.execute(text("""
        UPDATE compass_songs
        SET created_at = datetime(year || '-01-01 00:00:00', '+' || id || ' seconds')
        WHERE created_at IS NULL
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_compass_songs_created_at "
        "ON compass_songs(created_at DESC)"
    ))
