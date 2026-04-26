"""Ether Art Chart fields on library_songs.

Mirror of migration 042 against the library_songs table so the ether tagger
can run against either target from the Backfill Console:

  deadpan_line  TEXT  — flat literal naming of the song
  topics        TEXT  — JSON array of slugs from the 24-topic taxonomy
  topic_audit   TEXT  — JSON {reason, proposed_tag, rationale} when no
                        slug fits

Forward-only — every existing row stays NULL. The Backfill Console runs
the same calibrate-then-tag pipeline used by _store_calibration on
compass_songs, just writing into library_songs when that's the target.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("ALTER TABLE library_songs ADD COLUMN deadpan_line TEXT"))
    conn.execute(text("ALTER TABLE library_songs ADD COLUMN topics TEXT"))
    conn.execute(text("ALTER TABLE library_songs ADD COLUMN topic_audit TEXT"))
