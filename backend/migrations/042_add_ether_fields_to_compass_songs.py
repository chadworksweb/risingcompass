"""Ether Art Chart fields on compass_songs.

Adds three nullable columns the post-classification ether tagger writes to:

  deadpan_line  TEXT  — flat literal naming of the song (~artist+title length)
  topics        TEXT  — JSON array of slugs from the 24-topic taxonomy
                        (services/ether_taxonomy.py), ordered most-dominant-first
  topic_audit   TEXT  — JSON {reason, proposed_tag, rationale} when topics is
                        empty (the audit escape hatch); NULL otherwise

Forward-only rollout — existing rows stay NULL until the deferred backfill
runs. compass_songs only; reading_songs and submitted_songs are out of scope.
No new indexes — phase 1 surfaces don't filter on these columns.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("ALTER TABLE compass_songs ADD COLUMN deadpan_line TEXT"))
    conn.execute(text("ALTER TABLE compass_songs ADD COLUMN topics TEXT"))
    conn.execute(text("ALTER TABLE compass_songs ADD COLUMN topic_audit TEXT"))
