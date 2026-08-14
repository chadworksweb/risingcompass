"""Give a Release the rest of its v3 reading.

Migrations 069 + 090 gave `releases` nine reading columns (charge_summary,
arc_prose, both prose lanes, deadpan_line, topics, topic_audit, source,
submitted_at). Those nine were sized for the Album Charger's synthesis, which
compiled prose and nothing else. The rc-album LENS (built 2026-08-12) emits a
full v3 reading, and more than half of it has had nowhere to land:
contamination, doctrinal reference, confidence, the psyche-facts bundle and its
per-listen effects. Two albums have already been read against the instrument and
BOTH are stored half-complete, with the remainder parked in the binder as
`ALBUM-1349-STAGED-READING.json` / `ALBUM-1351-STAGED-READING.json`.

The column set is the SONG set (see `Song` in models.py), deliberately, so a
release row answers the same questions a song row does and every reader that
already knows how to render a song reading needs no new vocabulary:

  contaminated / contamination_note   the release-scale flag, settled under A2
                                      against the per-track findings, never a
                                      bare count of flagged tracks
  dogma_referenced / dogma_note       release-level doctrinal arc; genuinely a
                                      release read (an album can carry an arc no
                                      single track carries), and R14's raised
                                      Ascended bar applies to it
  confidence                          how firmly the release reads, which at
                                      album scale is mostly a question of how
                                      complete the song rows were
  psyche_facts / effects_pl           the prescription for taking in the WHOLE
                                      album, composed from the release's own
                                      finished reading (JSON-encoded Text, RC's
                                      convention for per-row JSON bundles)
  calibration_failed                  parity with the song row's failure marker
  societal_prose_* / prior_*          the prose seal + one-step-back archive,
                                      matching the song provenance contract so a
                                      release's societal prose carries the same
                                      tamper-evident stamp

One column has no song counterpart: `prior_arc_prose`. `arc_prose` is a
release-only lane that shipped in 069 with no archive slot at all, so without it
two of the three release prose lanes would archive and the third would overwrite
silently. The archive contract is uniform across all three or it is not a
contract.

The v3 COMPONENTS (visceral, coherence, harm/transcendence, center, vernier,
governing axis) deliberately do NOT land here. On the song side those live per
RUN on `calibration_runs`, not on the canonical row, because they are the
audit of a placement rather than the placement itself. Migration 149 gives the
album lane that same home.

PG-compatible (063+), idempotent. `Base.metadata.create_all()` builds these on
fresh installs from the model in app/models.py.
"""

from sqlalchemy import text

_COLS = (
    ("contaminated", "BOOLEAN DEFAULT FALSE"),
    ("contamination_note", "TEXT"),
    ("dogma_referenced", "BOOLEAN DEFAULT FALSE"),
    ("dogma_note", "TEXT"),
    ("confidence", "DOUBLE PRECISION"),
    ("psyche_facts", "TEXT"),
    ("effects_pl", "TEXT"),
    ("calibration_failed", "BOOLEAN DEFAULT FALSE"),
    ("societal_prose_generated_at", "TIMESTAMP"),
    ("societal_prose_model", "TEXT"),
    ("prior_listener_effects_prose", "TEXT"),
    ("prior_societal_effects_prose", "TEXT"),
    ("prior_arc_prose", "TEXT"),
    ("prior_societal_prose_generated_at", "TIMESTAMP"),
    ("prior_societal_prose_model", "TEXT"),
)


def up(conn):
    for column, coltype in _COLS:
        conn.execute(text(
            f"ALTER TABLE releases ADD COLUMN IF NOT EXISTS {column} {coltype}"
        ))
