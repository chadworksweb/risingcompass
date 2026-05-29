"""External tamper-evident anchors for societal-prose provenance.

The sealed generated_at + model on the song rows (migration 075) live in a DB
that whoever controls the DB can edit, so they are not by themselves
tamper-proof. This table is the bridge to an EXTERNAL, append-only anchor:

  1. A public GitHub repo carries a hash-only, append-only log (the prose text
     itself never leaves the DB -- only sha256(table:id | generated_at | model
     | prose) is published, so a sold/premium prose value can be proven later
     by revealing it and re-hashing, without publishing it now).
  2. Each batch committed to that repo is OpenTimestamped, anchoring its hash
     to a Bitcoin block. Bitcoin -- not the DB, not GitHub, not the author --
     becomes the trust root: it proves the prose existed no later than block N.

One row per sealed prose VERSION (re-generation = new generated_at = new hash =
new anchor). github_* / ots_* fill in as the batch is committed and then
confirmed on-chain by the sweep / upgrade crons. Fail-soft: nothing here is on
the calibration hot path; a missing/failed anchor never blocks a calibration.

PG-compatible (063+). Base.metadata.create_all() picks this up on fresh
installs from the ProseProvenanceAnchor model; this is the idempotent path for
the existing prod DB.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS prose_provenance_anchors ("
        "  id SERIAL PRIMARY KEY,"
        "  song_table TEXT NOT NULL,"
        "  song_id INTEGER NOT NULL,"
        "  prose_sha256 TEXT NOT NULL,"
        "  generated_at TIMESTAMP NOT NULL,"
        "  model TEXT NOT NULL,"
        "  sealed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  github_commit_sha TEXT,"
        "  github_committed_at TIMESTAMP,"
        "  ots_status TEXT NOT NULL DEFAULT 'pending',"
        "  ots_proof_path TEXT,"
        "  ots_bitcoin_block INTEGER,"
        "  ots_block_time TIMESTAMP"
        ")"
    ))
    # One anchor per (table, row, prose version). Re-running the sweep is a
    # no-op for already-anchored versions; a regenerated prose has a new hash
    # and so a new anchor row.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_prose_anchor_version "
        "ON prose_provenance_anchors (song_table, song_id, prose_sha256)"
    ))
    # The upgrade cron scans for proofs not yet confirmed on-chain.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_prose_anchor_ots_status "
        "ON prose_provenance_anchors (ots_status)"
    ))
