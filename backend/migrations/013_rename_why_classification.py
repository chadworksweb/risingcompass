"""Rename compass_songs.why_classification column to why_calibration.

Part of the classify → calibrate vocabulary refactor.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE compass_songs RENAME COLUMN why_classification TO why_calibration"
    ))
