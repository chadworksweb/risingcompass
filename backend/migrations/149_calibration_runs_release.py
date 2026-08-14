"""Let a calibration run point at a RELEASE, and record the album's coherence.

`calibration_runs` is the append-only ledger behind every reading: the v3
components, the composed charge, and the agent's stored argument. The rc-album
lens produces exactly that shape of artifact, but the table could only key a run
to a song, so the two albums read so far logged NO run at all. Their arguments
sit in the binder as loose text files (`ALBUM-1349-REASONING.txt`,
`ALBUM-1351-REASONING.txt`) instead of in the ledger, which means the release
rows carry a reading with no recoverable derivation -- the exact gap
`calibration_runs.reasoning` was added to close for songs.

Two columns:

  release_id   nullable FK, ON DELETE SET NULL, mirroring `song_id`. A run keys
               to a song OR a release; the title/artist snapshot already carries
               the identity either way. Nullable-and-SET-NULL because the ledger
               has to outlive a re-resolved catalogue (a rebuild churns
               `releases.id`).

  coherence    the album's structural verdict, "coherent" | "anthology": whether
               the tracks answer each other or merely sit side by side. It is the
               album's parallel of the song's `route` -- a lens-specific axis
               sharing the run table, exactly as `route` does, rather than a
               reason to fork the ledger.

The rest of the v3 component set (visceral_charge, harm_*, transcendence_value,
governing_axis, center, vernier, gut_divergence) is ALREADY on this table from
migration 116 and carries the album read unchanged: the album lens emits the
same components under the same names. Only the pointer and the coherence axis
were missing.

PG-compatible (063+), idempotent. `Base.metadata.create_all()` builds these on
fresh installs from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE calibration_runs ADD COLUMN IF NOT EXISTS release_id INTEGER"
    ))
    conn.execute(text(
        "ALTER TABLE calibration_runs "
        "ADD COLUMN IF NOT EXISTS coherence VARCHAR(20)"
    ))

    # FK added separately so a re-run on a DB that already has the column is a
    # no-op rather than an error. Postgres has no ADD CONSTRAINT IF NOT EXISTS.
    conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conname = 'fk_calibration_runs_release'
            ) THEN
                ALTER TABLE calibration_runs
                  ADD CONSTRAINT fk_calibration_runs_release
                  FOREIGN KEY (release_id) REFERENCES releases(id)
                  ON DELETE SET NULL;
            END IF;
        END $$;
    """))

    # Query shape is "the run history for this release", newest first -- the
    # same shape the Runs admin already uses for a song.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_calibration_runs_release "
        "ON calibration_runs (release_id, id DESC)"
    ))
