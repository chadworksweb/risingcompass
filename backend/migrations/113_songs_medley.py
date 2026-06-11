"""Add songs.medley -- parallel metadata tag, set when a calibration reads a
curated multi-song medley as a single arc (the charge is read on the assembled
sequence, not one authored song). Parallel to translated / instrumental /
contaminated / dogma_referenced: never moves the charge. Supplied from terminal
via calibrate_song.py --medley and mapped in song_sync.calibration_to_columns.

PG-compatible (063+). Base.metadata.create_all() builds the column on fresh
installs from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS medley BOOLEAN NOT NULL DEFAULT false"
    ))
