"""Add lyrics_fingerprint to calibration_runs for the Layer 2 divergence guard.

Stores a non-reversible MinHash signature of submitted lyrics so we can detect
"two runs claim the same (title, artist) but the lyrics are radically
different" without ever storing the raw text. Hard legal constraint: lyrics
text is never persisted; only a 512-byte fingerprint per run.

Pairs with the existing lyrics_hash column (sha256 of normalized text) — the
hash is exact-dupe-only; the fingerprint supports fuzzy similarity (Jaccard
estimation against prior runs on the same canonical row).

Forward-only. Existing rows stay NULL; the divergence check skips comparisons
against null fingerprints (we have no signal for those, so they neither
support nor refute a new submission).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE calibration_runs ADD COLUMN lyrics_fingerprint BLOB"
    ))
