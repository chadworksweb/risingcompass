"""Rename album_classifications table to album_calibrations + rename its unique index.

Part of the classify → calibrate vocabulary refactor.

Handles the case where Base.metadata.create_all() has already auto-created an
empty `album_calibrations` table (because it runs before migrations and the
model's __tablename__ is now the new name). The empty auto-created table is
dropped before the rename so the old table's data is preserved.
"""

from sqlalchemy import text


def up(conn):
    tables = {row[0] for row in conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).fetchall()}

    if "album_classifications" not in tables:
        # Fresh install — create_all() already made album_calibrations correctly.
        return

    if "album_calibrations" in tables:
        count = conn.execute(text("SELECT COUNT(*) FROM album_calibrations")).scalar()
        if count and count > 0:
            raise RuntimeError(
                "Both album_classifications and album_calibrations exist with data — "
                "manual intervention required to merge."
            )
        conn.execute(text("DROP TABLE album_calibrations"))

    conn.execute(text("ALTER TABLE album_classifications RENAME TO album_calibrations"))
    conn.execute(text("DROP INDEX IF EXISTS uq_album_classifications_title_artist"))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_album_calibrations_title_artist "
        "ON album_calibrations (title, artist)"
    ))
