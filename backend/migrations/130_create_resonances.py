"""Audience Resonance -- listener testimony sliced into a proportional verdict.

Audience Resonance is RC's fourth instrument (its OWN section, not part of
Audience Vibe). A person submits a Resonance about one already-scored song; the
slicer renders a PROPORTIONAL verdict across three buckets that sum to 100:
True (the song genuinely elevated them) / Camouflage (counterfeit lift in
disguise) / Adjacent (the meaning came from their own life, the song was the
bookmark). The point is to find whether a corrupted song can genuinely elevate
someone, and to be honest when it cannot. Like Audience Vibe, this NEVER
overrides the rubric -- it is signal, at most a review case.

Isolated table, FK to the unified songs(id) (submitted_songs was dropped in 088).
`slice_attribution` is the JSON-encoded line-by-line "shows its work".
consent_tier is publish | private (delete is a hard row removal, not a status).
flag_state tracks the "did we misread your story" review -- distinct from the
song-charge satire flag (misread_submissions). `is_synthetic` fences hand-authored
build/demo seed from real research aggregates.

PG-compatible (063+). Base.metadata.create_all() builds it on fresh installs from
the model in app/models.py; this creates it on existing DBs. Idempotent
(CREATE TABLE / INDEX IF NOT EXISTS).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS resonances (
            id                SERIAL PRIMARY KEY,
            song_id           INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
            user_id           INTEGER,
            username          VARCHAR(120) NOT NULL,
            story_text        TEXT NOT NULL,
            prop_true         INTEGER NOT NULL DEFAULT 0,
            prop_camouflage   INTEGER NOT NULL DEFAULT 0,
            prop_adjacent     INTEGER NOT NULL DEFAULT 0,
            slice_attribution TEXT,
            consent_tier      VARCHAR(20) NOT NULL DEFAULT 'private',
            flag_state        VARCHAR(20) NOT NULL DEFAULT 'none',
            is_synthetic      BOOLEAN NOT NULL DEFAULT FALSE,
            created_at        TIMESTAMP DEFAULT (now() at time zone 'utc'),
            updated_at        TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))

    # Per-song rollup (the headline count + ternary) reads by song.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_resonances_song ON resonances (song_id)"
    ))
    # Public corpus reads filter to published rows.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_resonances_consent ON resonances (consent_tier)"
    ))
    # The human-review queue scans flagged rows.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_resonances_flag ON resonances (flag_state)"
    ))
