"""Integrity re-verification tracking for prose provenance anchors.

The sweep/upgrade crons get a prose hash onto Bitcoin and mark it `complete`.
But a `complete` proof can still rot afterwards: the batch file in the public
repo could be corrupted or tampered, silently breaking the chain from the
published hash to its on-chain timestamp. The integrity re-verify cron re-runs
`ots verify` on a rolling sample of complete proofs to catch that. These two
columns record, per anchor, when it was last re-verified and the last result:

  - ots_last_verified_at  TIMESTAMP NULL  (when `ots verify` last ran for it)
  - ots_verify_status     TEXT NULL       ('ok' | 'mismatch' | 'inconclusive')

A 'mismatch' is the tampering/corruption signal and drives the
`provenance_integrity` alert + a health breach. NULL last_verified = never
re-verified yet (the cron round-robins least-recently-verified first).

PG-compatible (063+). Base.metadata.create_all() picks the columns up on fresh
installs from the updated ProseProvenanceAnchor model; this migration is the
idempotent path for the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE prose_provenance_anchors ADD COLUMN IF NOT EXISTS "
        "ots_last_verified_at TIMESTAMP"
    ))
    conn.execute(text(
        "ALTER TABLE prose_provenance_anchors ADD COLUMN IF NOT EXISTS "
        "ots_verify_status TEXT"
    ))
