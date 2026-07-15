"""Unit test for the human-confirmed alias bridge (rung 1b).

Some feeder strings are unreachable by every deterministic rung, and correctly so
-- widening a rung to catch them would false-merge real distinct works. Two live
cases (relinked daily through 2026-07-15) motivate the rung, and they are mirror
images of each other:

  title miss:  stored 'MORNING DEW (DONK)' / 'Beyonce'  vs feeder 'MORNING DEW'
      Artist keys match exactly; the title differs by a version marker.
      feeder_clean deliberately never strips version markers (a remix must stay a
      distinct work), so no cleaning rung can bridge this without breaking that
      guarantee.
  artist miss: stored 'Dancing with the Enemy' / 'Descendants Cast'
               vs feeder 'DisneyMusic'
      Title keys match exactly; the artist is a channel credit. Rung 2b needs a
      (From "...") title marker (absent here) and rung 2c needs a shared primary-
      artist token ({disneymusic} & {descendantscast} is empty).

Guards under test: the alias resolves each case; the exact key still outranks an
alias; an alias never leaks to a different song; and a missing table degrades to
the old ladder instead of bricking resolution (fail-soft).

Run standalone:  python tests/test_song_identity_alias.py
Or via pytest:   python -m pytest tests/test_song_identity_alias.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

from app.models import Base
from app.services.song_identity import (
    compute_canonical_key,
    compute_canonical_key_clean,
    resolve_song_identity,
)

STORED_DEW = ("MORNING DEW (DONK)", "Beyonce")
FEEDER_DEW = ("MORNING DEW", "Beyonce")
STORED_ENEMY = ("Dancing with the Enemy", "Descendants Cast")
FEEDER_ENEMY = ("Dancing with the Enemy", "DisneyMusic")


def _build_engine():
    for t in Base.metadata.sorted_tables:
        for col in t.columns:
            sd = col.server_default
            if sd is not None and "now(" in str(getattr(sd, "arg", "")).lower():
                col.server_default = None
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def _seed_song(c, sid, title, artist):
    c.execute(
        text(
            "INSERT INTO songs (id,title,artist,canonical_key,canonical_key_clean,"
            "rubric_color,charge_value) VALUES (:i,:t,:a,:k,:ck,'green',5)"
        ),
        {
            "i": sid, "t": title, "a": artist,
            "k": compute_canonical_key(title, artist),
            "ck": compute_canonical_key_clean(title, artist),
        },
    )


def _seed_alias(c, title, artist, song_id):
    """Alias keyed on the string the FEEDER sends (what relink_draft_song.py does)."""
    c.execute(
        text(
            "INSERT INTO song_identity_aliases "
            "(alias_key, song_id, alias_title, alias_artist, source) "
            "VALUES (:k,:s,:t,:a,'relink')"
        ),
        {"k": compute_canonical_key_clean(title, artist), "s": song_id,
         "t": title, "a": artist},
    )


def test_alias_resolves_title_and_artist_misses():
    eng = _build_engine()
    with eng.begin() as c:
        _seed_song(c, 3790, *STORED_DEW)
        _seed_song(c, 4008, *STORED_ENEMY)

        # Precondition: both are genuinely unreachable before the alias exists.
        # If either of these ever starts resolving, the rung is being masked by
        # another rung and this test would silently stop proving anything.
        for feeder in (FEEDER_DEW, FEEDER_ENEMY):
            r = resolve_song_identity(c, *feeder)
            assert r.song_id is None, f"{feeder} resolved pre-alias via {r.via}"
            assert r.via == "new"

        _seed_alias(c, *FEEDER_DEW, song_id=3790)
        _seed_alias(c, *FEEDER_ENEMY, song_id=4008)

        r = resolve_song_identity(c, *FEEDER_DEW)
        assert (r.song_id, r.via) == (3790, "alias"), r

        r = resolve_song_identity(c, *FEEDER_ENEMY)
        assert (r.song_id, r.via) == (4008, "alias"), r
    print("ok: alias resolves both the title miss and the artist miss")


def test_exact_key_outranks_alias():
    """Rung 1 must still win: an alias can never override a live canonical_key."""
    eng = _build_engine()
    with eng.begin() as c:
        _seed_song(c, 3790, *STORED_DEW)
        _seed_song(c, 4008, *STORED_ENEMY)
        # A (mistaken) alias pointing the STORED string at the wrong song.
        _seed_alias(c, *STORED_DEW, song_id=4008)

        r = resolve_song_identity(c, *STORED_DEW)
        assert (r.song_id, r.via) == (3790, "exact"), r
    print("ok: exact canonical_key still outranks an alias")


def test_alias_does_not_leak_to_other_songs():
    """An alias is scoped to its own key -- no false merge onto a neighbour."""
    eng = _build_engine()
    with eng.begin() as c:
        _seed_song(c, 3790, *STORED_DEW)
        _seed_song(c, 4008, *STORED_ENEMY)
        _seed_alias(c, *FEEDER_DEW, song_id=3790)

        # A different song on the same channel credit must NOT ride the alias.
        r = resolve_song_identity(c, "Some Other Disney Song", "DisneyMusic")
        assert r.song_id is None, r
        # A real remix of the aliased title stays its own work.
        r = resolve_song_identity(c, "MORNING DEW (Remix)", "Beyonce")
        assert r.song_id is None, r
    print("ok: alias does not leak to other songs or to a real remix")


def test_missing_table_is_fail_soft():
    """A stale container without the table must degrade to the old ladder."""
    eng = _build_engine()
    with eng.begin() as c:
        _seed_song(c, 3790, *STORED_DEW)
        c.execute(text("DROP TABLE song_identity_aliases"))

        # Exact still resolves; the unreachable string still falls through to new.
        r = resolve_song_identity(c, *STORED_DEW)
        assert (r.song_id, r.via) == (3790, "exact"), r
        r = resolve_song_identity(c, *FEEDER_DEW)
        assert r.song_id is None and r.via == "new", r
    print("ok: missing alias table degrades to the old ladder")


if __name__ == "__main__":
    test_alias_resolves_title_and_artist_misses()
    test_exact_key_outranks_alias()
    test_alias_does_not_leak_to_other_songs()
    test_missing_table_is_fail_soft()
    print("\nall alias-rung tests passed")
