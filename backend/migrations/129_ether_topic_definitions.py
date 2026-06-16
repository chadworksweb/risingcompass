"""Admin Taxonomy Editor (Phase 2a) -- add the tagger DEFINITION columns to
ether_topics.

  scope     -- the one-line topic meaning the classifier reads (TEXT)
  examples  -- JSON list of {artist, title, why} anchoring examples

These make the DB able to drive the live ether tagger prompt + valid-slug set.
The cutover is flag-gated (`taxonomy_db_driven.enabled`, fail-closed) so the
tagger keeps reading the code constants until an admin flips it; columns alone
change nothing. An idempotent startup backfill
(ether_taxonomy.backfill_topic_definitions) fills scope/examples for the
code-seeded topics that predate these columns.

PG-compatible (063+). Idempotent (ADD COLUMN IF NOT EXISTS).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "ALTER TABLE ether_topics ADD COLUMN IF NOT EXISTS scope TEXT"
    ))
    conn.execute(text(
        "ALTER TABLE ether_topics ADD COLUMN IF NOT EXISTS examples JSON"
    ))
