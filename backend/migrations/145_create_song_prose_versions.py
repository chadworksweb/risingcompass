"""Deep history for a song's generated prose.

`calibration_runs` is append-only for the CALIBRATION (color, charge, summary,
reasoning, the v3 components), so a re-read is always reconstructable. Prose had
nothing comparable: `songs.prior_listener_effects_prose` /
`prior_societal_effects_prose` are ONE slot each, and `psyche_facts` has no
archive column at all. Three regens in a row leave the live text plus whatever
was live immediately before it, and everything earlier is gone for good.

That is not theoretical. On 2026-08-12 song 2797 was re-run orange -28 -> green
+6 and its prose regenerated; a stripped first attempt, the clean rewrite, and a
topics retag pushed the original orange-era prose out of the single archive slot
inside three writes. The provenance anchor keeps only a hash, so the text was not
recoverable from anywhere in the database.

Worse, the ONE archive step lives in the two explicit regen routers
(`prose_admin`, `recalibrations`). The chokepoint every authoritative calibration
actually goes through (`song_sync.upsert_unified_song`, where the prose columns
ride the `_CALIB` list) overwrites prose with no archive at all.

This table is the deep history. `prior_*` stays exactly as it is (the badge, the
song admin page, and `song_merge`'s column list all read it), so nothing
downstream changes.

Spec: `Dropbox/Rising Compass/plans and docs/RISING-COMPASS-PROSE-VERSIONING-SCOPE.md`

Idempotent; PG-compatible (063+). Base.metadata.create_all() builds the table on
fresh installs from the models.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_prose_versions (
            id             SERIAL PRIMARY KEY,
            written_at     TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),

            -- Nullable and deliberately NOT a foreign key: the history has to
            -- outlive the song (merge, delete, orphan cleanup), same posture as
            -- draft_song_edits. title + artist snapshot so an orphaned version
            -- is still identifiable.
            song_id        INTEGER,
            title          TEXT,
            artist         TEXT,

            -- listener | societal | psyche_facts
            lane           VARCHAR(20) NOT NULL,
            prose          TEXT NOT NULL,

            -- Provenance of THIS version, copied from the seal at write time.
            model          VARCHAR(80),
            generated_at   TIMESTAMP,

            -- Which write produced it: terminal_regen | admin_recal |
            -- chart_reading | terminal | lyrical_charger | catalog_backfill |
            -- stream | backfill_seed
            trigger        VARCHAR(40),

            -- The read this prose was written FOR. A prose block arguing a tier
            -- the song no longer carries is the failure this column makes
            -- visible without reading the text.
            rubric_color   VARCHAR(20),
            charge_value   INTEGER,

            environment    VARCHAR(16) NOT NULL DEFAULT 'prod'
        )
    """))

    # Query shape is "history for this song" and "what has been written lately".
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_song_prose_versions_song "
        "ON song_prose_versions (song_id, id DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_song_prose_versions_written "
        "ON song_prose_versions (written_at DESC)"
    ))
