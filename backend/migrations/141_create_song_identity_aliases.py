"""Create song_identity_aliases -- the human-confirmed identity bridge (rung 1b).

The problem this closes. The identity ladder (app/services/song_identity.py)
resolves a feeder's (title, artist) to a Library row via the exact canonical_key,
then a cleaned key, then two deterministic bridges, then a dark trgm rung. When a
feeder persistently sends a string none of those rungs can reach, the song lands
on the awaiting-lyrics list EVERY day and a human relinks it EVERY day. The relink
is a human asserting "this string IS that song" -- and until now that assertion was
thrown away the moment the script exited, so the same relink recurred forever.

Two live cases (both relinked daily through 2026-07-15) motivated this table, and
they are mirror images:
  "MORNING DEW" / "Beyonce"            -> song 3790 "MORNING DEW (DONK)" / "Beyonce"
      Artist matches exactly; the TITLE differs. Song 3790 was born on iTunes,
      which carries Apple's "(DONK)" suffix; YouTube sends the plain title.
      feeder_clean deliberately never strips version markers (a remix must stay a
      distinct work), so no cleaning rung can or should bridge this.
  "Dancing with the Enemy" / "DisneyMusic" -> song 4008 / "Descendants Cast"
      Title matches exactly; the ARTIST differs. The row was born on YouTube and
      its artist was hand-cleaned off the channel credit at calibration time --
      which is precisely what orphaned the feeder's string. Rung 2b needs a
      (From "...") title marker (absent); rung 2c needs a shared artist token
      ({disneymusic} & {descendantscast} is empty).
Measured against prod, the trgm rung rescues neither even with autolink forced on:
MORNING DEW scores title 0.625 (auto needs >= 0.92); Dancing scores artist 0.037
(auto needs >= 0.90).

Why an alias table rather than widening a rung. Every widening that would catch
these also carries false-merge risk on real distinct works (stripping "(DONK)"
would collapse a genuine remix into its original; matching exact-title + generic
channel would collapse two different songs that share a common title). An alias
row is human-confirmed, so it carries none of that risk, and it is
self-extinguishing: the relink that creates it is the last relink for that string.

`alias_key` is the CLEAN key (compute_canonical_key_clean) of the string the
feeder actually sent -- clean rather than exact so a day-to-day formatting wobble
in the same feeder string still lands, and clean-keyed because that normalizer
folds diacritics (the exact key drops them, so "Beyonce" with an accent keys to
"beyonc"). UNIQUE, so one incoming identity can never point at two songs.

PG-compatible (063+). Base.metadata.create_all() builds this on fresh installs
from the model in app/models.py.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS song_identity_aliases ("
        "  id SERIAL PRIMARY KEY,"
        "  alias_key TEXT NOT NULL,"
        "  song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,"
        "  alias_title TEXT,"
        "  alias_artist TEXT,"
        "  source TEXT NOT NULL DEFAULT 'relink',"
        "  notes TEXT,"
        "  created_at TIMESTAMP DEFAULT NOW()"
        ")"
    ))
    # One incoming identity resolves to exactly one song. The UNIQUE also serves
    # as the lookup index for rung 1b (the only read is an equality on alias_key).
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_song_identity_aliases_key "
        "ON song_identity_aliases (alias_key)"
    ))
    # Merge repoints aliases onto the survivor; this index keeps that cheap.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_song_identity_aliases_song "
        "ON song_identity_aliases (song_id)"
    ))
