"""Add songs.translated -- a parallel provenance flag, set when a song was
calibrated off a TRANSLATION of non-English lyrics rather than its original
words. Parallel to contaminated / dogma_referenced / instrumental: a metadata
tag that never moves the charge. Supplied from terminal via
calibrate_song.py --translated and mapped onto the songs row in
song_sync.calibration_to_columns / _CALIB.

PG-compatible (063+). Base.metadata.create_all() builds the column on fresh
installs from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS translated BOOLEAN NOT NULL DEFAULT false"
    ))
