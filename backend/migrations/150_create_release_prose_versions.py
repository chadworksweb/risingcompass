"""Deep history for a release's generated prose. The album twin of migration 145.

Everything 145 argued for songs applies here with more force, not less. A
release carries THREE prose lanes (arc, listener, societal) plus the psyche
facts bundle; migration 148 gives each one a single `prior_*` slot, and one slot
is one regen deep. On the song side that shallow archive lost real text inside
three writes (song 2797, 2026-08-12) and the provenance anchor keeps only a
hash, so nothing was recoverable. A release is worse exposed: its prose is
composed from the whole running order, so re-composing it is not a re-read of one
lyric sheet but a re-read of twenty approved rows.

Same posture as 145, deliberately identical so the two histories read as one
system:
  - `release_id` is nullable and NOT a foreign key, so the history outlives the
    row. This matters more for releases than for songs: a catalogue rebuild
    CHURNS `releases.id` by design (see "Rebuilding a catalogue" in CLAUDE.md),
    so a release's prose history would be orphaned by routine maintenance, not
    just by deletion. title + artist snapshot keeps an orphan identifiable.
  - the read the prose was written FOR (`rubric_color` + `charge_value`) rides
    along, so prose arguing a tier the release no longer carries is a column
    comparison rather than a reading of the text.
  - `environment` tags local vs prod, since local dev shares the prod DB.

Lanes: arc | listener | societal | psyche_facts. `topics` and `effects_pl` stay
out for the same reason they do on the song side: they are tags, cheap to
re-derive, and the run ledger already records the read they were chosen under.

PG-compatible (063+), idempotent. `Base.metadata.create_all()` builds the table
on fresh installs from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS release_prose_versions (
            id             SERIAL PRIMARY KEY,
            written_at     TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),

            -- Nullable and deliberately NOT a foreign key: a catalogue rebuild
            -- re-creates releases with new ids, so the history has to survive
            -- an id that no longer exists. title + artist snapshot so an
            -- orphaned version is still identifiable.
            release_id     INTEGER,
            title          TEXT,
            artist         TEXT,

            -- arc | listener | societal | psyche_facts
            lane           VARCHAR(20) NOT NULL,
            prose          TEXT NOT NULL,

            -- Provenance of THIS version, copied from the seal at write time.
            model          VARCHAR(80),
            generated_at   TIMESTAMP,

            -- Which write produced it: terminal_album | album_charger |
            -- admin_recal | terminal_regen
            trigger        VARCHAR(40),

            -- The read this prose was written FOR.
            rubric_color   VARCHAR(20),
            charge_value   INTEGER,

            environment    VARCHAR(16) NOT NULL DEFAULT 'prod'
        )
    """))

    # Query shape is "history for this release" and "what has been written
    # lately" -- the same two the song table serves.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_release_prose_versions_release "
        "ON release_prose_versions (release_id, id DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_release_prose_versions_written "
        "ON release_prose_versions (written_at DESC)"
    ))
