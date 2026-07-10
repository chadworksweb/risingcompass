"""Add songs.psyche_facts -- the Psyche Facts family bundle (the "Drug Facts"
prescription layer for a song), stored as a JSON-encoded Text column exactly
like topics / topic_audit / activations (RC's convention for per-song JSON
bundles; the badge's _parse_json decodes it). Holds the sibling keys purpose,
indicated_for[], do_not_use_if, directions, onset, duration, warning. The
psyche_effects tag axis joins this family later, once its vocabulary is
re-derived against a corpus sample.

Supplied from terminal via calibrate_song.py --psyche-facts-file and mapped onto
the songs row in song_sync.calibration_to_columns / _CALIB (json.dumps'd, same as
topics). NULL until a calibration carries it; a re-read without it never nulls an
existing value (only_set_present in upsert_unified_song).

PG-compatible (063+). Base.metadata.create_all() builds the column on fresh
installs from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS psyche_facts TEXT"
    ))
