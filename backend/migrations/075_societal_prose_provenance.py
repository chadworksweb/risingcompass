"""Sealed generation provenance for societal_effects_prose.

Every societal_effects_prose value gets its own generation timestamp + the
model that produced it, captured at the moment generation succeeds -- NOT at
row insert. This underpins the forward-locked "prophecy" instrument: we must
be able to prove WHEN a given prose was written, independent of created_at.

Two columns on every table that carries societal_effects_prose:
  - societal_prose_generated_at  TIMESTAMP NULL  (sealed at generation success)
  - societal_prose_model         TEXT NULL       (the model that produced it)

Tables (four; AgentDraftSong has no societal_effects_prose, so it is skipped):
  compass_songs, library_songs, submitted_songs, stream_songs.

One-time backfill for rows that already carry prose but predate provenance:
generated_at is set to the row's own created_at (submitted_at on
submitted_songs) as a PROXY, and model to 'legacy_unknown'. These are PROXY
timestamps, NOT sealed provenance -- they record only when the row was first
written, which is the best approximation available for prose generated before
this column existed. New prose written after this migration carries the real
sealed generated_at, so the proxy rows are distinguishable by their
'legacy_unknown' model tag.

PG-compatible (063+). Base.metadata.create_all() picks the new columns up on
fresh installs from the updated models; this migration is the idempotent path
for the existing prod DB.
"""

from sqlalchemy import text


# (table, timestamp column used as the legacy proxy)
_PROSE_TABLES = [
    ("compass_songs", "created_at"),
    ("library_songs", "created_at"),
    ("submitted_songs", "submitted_at"),
    ("stream_songs", "created_at"),
]


def up(conn):
    for table, ts_col in _PROSE_TABLES:
        conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            f"societal_prose_generated_at TIMESTAMP"
        ))
        conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            f"societal_prose_model TEXT"
        ))
        # One-time PROXY backfill for pre-provenance rows. Proxy = the row's
        # own insert timestamp; this is NOT sealed provenance, only the best
        # approximation for prose written before this column existed. Idempotent
        # via the generated_at IS NULL guard so re-running never overwrites a
        # real sealed timestamp.
        conn.execute(text(
            f"UPDATE {table} "
            f"SET societal_prose_generated_at = {ts_col}, "
            f"    societal_prose_model = 'legacy_unknown' "
            f"WHERE societal_effects_prose IS NOT NULL "
            f"  AND societal_effects_prose <> '' "
            f"  AND societal_prose_generated_at IS NULL"
        ))
