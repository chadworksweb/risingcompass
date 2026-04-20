"""Song resets — append-only audit log of calibrations returned to null.

A reset takes a previously-calibrated song back to the uncalibrated state:
charge_value, rubric_color, charge_summary, contaminated, contamination_note
are nulled. The row persists (id stable, song_artists / release_songs
linkages survive) so the song can be re-submitted through the normal
pipeline later.

Distinct from song_recalibrations because those always carry a *new* charge
(satire re-read, public-interest re-read). Resets go the other way — back
to zero — which wouldn't satisfy after_charge NOT NULL on recalibrations.

Immutable. Surfaces on the public song detail page's history section in
the same vein as the calibration log — honesty about what changed and why.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            before_charge INTEGER,
            before_color VARCHAR(20),
            before_summary TEXT,
            before_contaminated BOOLEAN,
            before_contamination_note TEXT,
            reason TEXT NOT NULL,
            reset_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_song_resets_song "
        "ON song_resets(song_source, song_id)"
    ))
