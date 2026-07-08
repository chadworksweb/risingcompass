"""Unit test for the shared-primary-artist bridge (rung 2c).

A song already in the Library under a LONE (often non-lead) primary artist gets
re-listed as awaiting-lyrics every day when a feeder surfaces it with the FULL,
reordered credit: neither the lead key nor the co-primary SET key matches,
because the stored artist is a single token and the incoming credit a different
(larger) set. Rung 2c resolves it when the clean TITLE matches exactly AND the
two credits share at least one normalized primary-artist token.

Real cases this reproduces (2026-07-08 YouTube feeder):
  'met me sooner'      stored 'TopOppGen'      vs feeder 'FattMack, TopOppGen'
  'Neighborhood Starz' stored 'Rylo Rodriguez' vs feeder 'Kevin Gates, Rylo Rodriguez, & Lil Baby'

Guards under test: no shared artist -> stays separate (no false merge); the flag
disables the rung; the exact fast path is untouched.

Run standalone:  python tests/test_song_identity_shared_artist.py
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


def _build_engine():
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
            "rubric_color,charge_value) VALUES (:i,:t,:a,:k,:ck,'red',-88)"
        ),
        {
            "i": sid, "t": title, "a": artist,
            "k": compute_canonical_key(title, artist),
            "ck": compute_canonical_key_clean(title, artist),
        },
    )


def _seed_flag(c, value):
    c.execute(
        text("INSERT INTO system_flags (key,value) VALUES "
             "('identity_shared_artist.enabled', :v)"),
        {"v": value},
    )


def test_shared_artist_resolves_full_credit():
    # met me sooner: stored lone secondary artist, feeder full credit.
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 3757, "met me sooner", "TopOppGen")
    with eng.connect() as c:
        res = resolve_song_identity(c, "met me sooner", "FattMack, TopOppGen")
        assert res.song_id == 3757 and res.via == "shared_artist", res

    # Neighborhood Starz: stored middle-credited artist, feeder full 3-way credit.
    eng2 = _build_engine()
    with eng2.begin() as c:
        _seed(c, 3797, "Neighborhood Starz", "Rylo Rodriguez")
    with eng2.connect() as c:
        res = resolve_song_identity(
            c, "Neighborhood Starz", "Kevin Gates, Rylo Rodriguez, & Lil Baby")
        assert res.song_id == 3797 and res.via == "shared_artist", res
    print("OK full/reordered credit resolves to the lone-artist Library row")


def test_symmetric_stored_full_incoming_lone():
    # Stored full credit, incoming lone member -> also resolves (symmetric).
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 400, "met me sooner", "FattMack, TopOppGen")
    with eng.connect() as c:
        res = resolve_song_identity(c, "met me sooner", "TopOppGen")
        assert res.song_id == 400 and res.via == "shared_artist", res
    print("OK symmetric: lone incoming credit resolves to a full-credit row")


def test_no_shared_artist_stays_separate():
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 100, "met me sooner", "Some Other Guy")
    with eng.connect() as c:
        res = resolve_song_identity(c, "met me sooner", "FattMack, TopOppGen")
        assert res.song_id is None and res.via == "new", res
    print("OK same title, no shared artist -> mints its own row (no false merge)")


def test_exact_still_fast_paths():
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 100, "met me sooner", "FattMack, TopOppGen")
    with eng.connect() as c:
        res = resolve_song_identity(c, "met me sooner", "FattMack, TopOppGen")
        assert res.song_id == 100 and res.via == "exact", res
    print("OK identical credit still hits the exact fast path")


def test_flag_disables_rung():
    eng = _build_engine()
    with eng.begin() as c:
        _seed(c, 3757, "met me sooner", "TopOppGen")
        _seed_flag(c, "false")
    with eng.connect() as c:
        res = resolve_song_identity(c, "met me sooner", "FattMack, TopOppGen")
        assert res.song_id is None and res.via == "new", res
    print("OK explicit flag=false disables the rung")


if __name__ == "__main__":
    test_shared_artist_resolves_full_credit()
    test_symmetric_stored_full_incoming_lone()
    test_no_shared_artist_stays_separate()
    test_exact_still_fast_paths()
    test_flag_disables_rung()
    print("all shared-artist-identity tests passed")
