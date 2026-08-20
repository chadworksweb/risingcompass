"""Record when a release's MBID resolve was attempted and definitively missed.

THE FAILURE THIS FIXES. The nightly cover-art sweep takes the first 15 pending
releases by `(charge_summary IS NULL), id` and re-resolves them every night. It
recorded nothing about a failure, so a release that can NEVER resolve came back
in the same 15 slots every single night and the queue behind it never moved. On
2026-08-20 the queue held 36 pending releases and the front 15 were permanently
unresolvable, eleven of them the synthetic "Singles & Uncategorized" catch-all
bucket, which has no MusicBrainz counterpart by construction. Three consecutive
nightly runs logged checked=15 attached=0 not_found=15. Positions 16 to 36 were
unreachable, so a newly created release could sit at the back of that queue and
never be resolved at all -- which is exactly what happened to a terminal album
read, whose art had to be attached by hand.

The song lane already solved this: `songs.release_group_checked_at` is stamped on
a miss and `pending_songs` filters on it being NULL, with `--recheck-misses` to
deliberately revisit. This is the same column for releases, so the two lanes
behave the same way.

STAMPED ON not_found ONLY, NEVER ON ambiguous. That distinction is the whole
point and inverting it would make things worse. An ambiguous release is one
MusicBrainz has several equally-good candidates for, and it becomes resolvable
the moment its own tracks resolve, because `_track_mbid_hint` then breaks the
tie. Stamping ambiguous would permanently skip precisely the releases that were
about to become answerable. Ambiguous stays in the queue on purpose.

Nullable, no default, no backfill: a NULL means never attempted, which is true of
every row predating this migration. Additive so it is metadata-only on Postgres
and older code that does not know the column keeps working. create_all() builds
it from the Release model on fresh installs; this is the explicit, idempotent add
for existing databases. PG-compatible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE releases ADD COLUMN IF NOT EXISTS mbid_checked_at TIMESTAMPTZ"
    ))
