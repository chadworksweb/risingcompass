"""Rename album_classifications table to album_calibrations + rename its unique index.

Part of the classify → calibrate vocabulary refactor.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("ALTER TABLE album_classifications RENAME TO album_calibrations"))
    conn.execute(text("DROP INDEX IF EXISTS uq_album_classifications_title_artist"))
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_album_calibrations_title_artist "
        "ON album_calibrations (title, artist)"
    ))
