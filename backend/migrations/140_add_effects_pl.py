"""Add songs.effects_pl -- the per-listen effects tag axis (the "psyche_effects
tag" the Psyche Facts family reserved). A JSON-encoded Text column holding a list
of SLUGS from the closed, RC-owned vocabulary in app/services/effects_pl_vocab.py,
stored exactly like topics (RC's convention for per-song JSON lists; the badge's
_parse_json decodes it, and the badge also resolves effects_pl_labels).

RC owns this vocabulary so it is the single source; chadlewine / the DBM
proliferator pull the labels from the badge instead of keeping their own list.
Slugs are the stable key -- a label reword never orphans a song's assignment.

Supplied from terminal via calibrate_song.py --effect-pl and the prose/regenerate
endpoint (regenerate_prose.py --effect-pl), validated against VALID_EFFECTS_PL, and
mapped onto the songs row in song_sync.calibration_to_columns / _CALIB (json.dumps'd,
same as topics). NULL until a calibration carries it; a re-read without it never
nulls an existing value (only_set_present in upsert_unified_song).

PG-compatible (063+). Base.metadata.create_all() builds the column on fresh
installs from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE songs ADD COLUMN IF NOT EXISTS effects_pl TEXT"
    ))
