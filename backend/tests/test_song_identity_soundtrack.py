"""Unit test for the soundtrack-cast identity bridge (rung 2b).

A soundtrack track '... (From "Show")' credited to a cast/ensemble surfaces with
an UNSTABLE credit string: a channel may collapse the full member credits
('Kylie Cantrall, Malia Baker, & Descendants Wicked Wonderland - Cast') to a
short label ('Descendants - Cast'). The two are genuinely different token sets,
so neither the set clean key nor the lead key matches across the collapse and the
song re-listed as awaiting-lyrics daily. Rung 2b matches on the globally unique
clean title part when the soundtrack marker AND a generic ensemble credit are
both present.

Guards under test: a NAMED cover of the same soundtrack song still mints its own
row (ensemble gate), and a plain (non-soundtrack) same-title / different-artist
pair never merges (marker gate).

Run standalone:  python tests/test_song_identity_soundtrack.py
Or via pytest:   python -m pytest tests/test_song_identity_soundtrack.py
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

ST_TITLE = 'Perfect Princess (From "Descendants: Wicked Wonderland")'
FULL_ARTIST = "Kylie Cantrall, Malia Baker, & Descendants Wicked Wonderland - Cast"
SHORT_ARTIST = "Descendants \u2013 Cast"  # en-dash, exactly as the live YouTube feed sends it


def _build_engine():
    # Strip ONLY the PG now()-expression server-defaults (invalid SQLite DDL).
    for t in Base.metadata.sorted_tables:
        for col in t.columns:
            sd = col.server_default
            if sd is not None and "now(" in str(getattr(sd, "arg", "")).lower():
                col.server_default = None
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def _seed(c, sid, title, artist):
    c.execute(
        text(
            "INSERT INTO songs (id,title,artist,canonical_key,canonical_key_clean,"
            "rubric_color,charge_value) VALUES (:i,:t,:a,:k,:ck,'green',10)"
        ),
        {
            "i": sid, "t": title, "a": artist,
            "k": compute_canonical_key(title, artist),
            "ck": compute_canonical_key_clean(title, artist),
        },
    )


def test_soundtrack_cast_collapse_resolves_both_directions():
    # Stored full credits, incoming short label -> resolves to the stored row.
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 100, ST_TITLE, FULL_ARTIST)
    with eng.connect() as c:
        res = resolve_song_identity(c, ST_TITLE, SHORT_ARTIST)
        assert res.song_id == 100, res
        assert res.via == "clean", res

    # Stored short label, incoming full credits -> also resolves (symmetric).
    eng2 = _build_engine()
    with eng2.begin() as c:
        _seed(c, 200, ST_TITLE, SHORT_ARTIST)
    with eng2.connect() as c:
        res = resolve_song_identity(c, ST_TITLE, FULL_ARTIST)
        assert res.song_id == 200, res
    print("OK soundtrack-cast collapse resolves both directions")


def test_exact_still_fast_paths():
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 100, ST_TITLE, FULL_ARTIST)
    with eng.connect() as c:
        res = resolve_song_identity(c, ST_TITLE, FULL_ARTIST)
        assert res.song_id == 100 and res.via == "exact", res
    print("OK identical credit still hits the exact fast path")


def test_named_cover_does_not_merge():
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 100, ST_TITLE, FULL_ARTIST)
    with eng.connect() as c:
        # A NAMED performer (no cast/ensemble credit) is a distinct recording.
        res = resolve_song_identity(c, ST_TITLE, "Some Singer")
        assert res.song_id is None and res.via == "new", res
    print("OK named cover of the soundtrack song mints its own row")


def test_plain_same_title_does_not_merge():
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 100, "Perfect Princess", "Band A")
    with eng.connect() as c:
        # No soundtrack marker -> rung 2b never fires; plain same-title songs by
        # different artists stay separate.
        res = resolve_song_identity(c, "Perfect Princess", "Band B")
        assert res.song_id is None and res.via == "new", res
    print("OK plain same-title / different-artist stays separate")


if __name__ == "__main__":
    test_soundtrack_cast_collapse_resolves_both_directions()
    test_exact_still_fast_paths()
    test_named_cover_does_not_merge()
    test_plain_same_title_does_not_merge()
    print("all soundtrack-identity tests passed")
