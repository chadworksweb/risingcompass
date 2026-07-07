"""Unit tests for the Phase-1 song identity-resolution cleaning (no DB).

Covers feeder_clean.clean_title_artist + song_identity.compute_canonical_key_clean:
the two 2026-06-13 misses must produce a MATCHING clean key across their
stored-row vs feeder-string formatting, and the closed token list must not eat
real title content (a song titled "Audio") or version-meaningful words (remix),
nor mangle a normal apostrophe title (Rock 'n' Roll).

Run standalone:  python tests/test_feeder_clean.py
Or via pytest:   python -m pytest tests/test_feeder_clean.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.feeder_clean import (
    clean_title_artist, is_feeder_upload, clean_feeder_display,
)
from app.services.song_identity import (
    compute_canonical_key,
    compute_canonical_key_clean,
    compute_canonical_key_clean_lead,
    resolve_song_identity,
)


def _same_clean(a, b):
    """The two (title, artist) pairs resolve to the SAME clean key."""
    return compute_canonical_key_clean(*a) == compute_canonical_key_clean(*b)


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row

    def fetchall(self):
        return []

    def scalar(self):
        return None


class _FakeDB:
    """Emulates just the two raw-SQL queries resolve_song_identity issues (Rung 1
    exact, Rung 2 clean), against an in-memory list of stored rows. No real DB --
    keeps this suite dependency-free while still testing resolution behavior."""

    def __init__(self, rows):
        self.rows = rows  # each: {id, canonical_key, canonical_key_clean}

    def execute(self, stmt, params=None):
        sql = str(stmt)
        p = params or {}
        if "WHERE canonical_key = :k LIMIT 1" in sql:  # Rung 1: exact
            hit = next((r for r in self.rows if r["canonical_key"] == p["k"]), None)
            return _FakeResult((hit["id"],) if hit else None)
        if "canonical_key_clean" in sql and "ORDER BY id" in sql:  # Rung 2: clean
            ck, lk, k = p.get("ck"), p.get("lk"), p.get("k")
            matched = [
                r for r in self.rows
                if (r.get("canonical_key_clean") in (ck, lk)
                    or r["canonical_key"] in (ck, lk))
                and r["canonical_key"] != k
            ]
            matched.sort(key=lambda r: r["id"])
            return _FakeResult((matched[0]["id"],) if matched else None)
        return _FakeResult(None)  # trgm rung / flag lookups: fall through


def _stored_row(rid, title, artist):
    return {
        "id": rid,
        "canonical_key": compute_canonical_key(title, artist),
        "canonical_key_clean": compute_canonical_key_clean(title, artist),
    }


def test_case1_kpop_label_channel_resolves():
    # Stored row id 3297 (a label-channel MV upload that cleans to the LEAD group
    # only) vs today's full-credit draft string. The set clean keys diverge by
    # design (subset vs full set), so resolution rides the lead-primary clean key.
    stored = ("ILLIT 'ICONIC BY MISTAKE' Official MV", "HYBE LABELS")
    draft = ("ICONIC BY MISTAKE", "ILLIT, LE SSERAFIM, KATSEYE")
    assert clean_title_artist(*stored) == ("ICONIC BY MISTAKE", "ILLIT")
    # The lead-primary clean key is the bridge between the two.
    assert (compute_canonical_key_clean_lead(*draft)
            == compute_canonical_key_clean(*stored)), (
        compute_canonical_key_clean_lead(*draft), compute_canonical_key_clean(*stored))
    # End to end: the draft resolves to the stored row via the clean rung.
    db = _FakeDB([_stored_row(3297, *stored)])
    res = resolve_song_identity(db, *draft)
    assert res.song_id == 3297 and res.via == "clean", (res.song_id, res.via)


def test_reorder_collab_still_matches_on_set_key():
    # The set clean key must still collapse a reordered full-credit collab, so the
    # lead-key addition does not regress order-independence.
    stored = ("ICONIC BY MISTAKE", "ILLIT, LE SSERAFIM, KATSEYE")
    draft = ("ICONIC BY MISTAKE", "KATSEYE, ILLIT, LE SSERAFIM")
    assert _same_clean(stored, draft)
    db = _FakeDB([_stored_row(3297, *stored)])
    res = resolve_song_identity(db, *draft)
    assert res.song_id == 3297, (res.song_id, res.via)


def test_distinct_lead_not_falsely_merged_by_lead_key():
    # The lead-key clause must not merge a genuinely different song that shares a
    # title but has a different lead artist.
    stored = ("Hold On", "Wilson Phillips")
    draft = ("Hold On", "Justin Bieber, Some Other Act")
    db = _FakeDB([_stored_row(40, *stored)])
    res = resolve_song_identity(db, *draft)
    assert res.song_id is None and res.via == "new", (res.song_id, res.via)


def test_case2_vevo_and_bracket_matches():
    # Stored row id 3311 vs today's draft string (identical title, VEVO artist).
    stored = ("Olivia Rodrigo - stupid song (Official Music Video)", "OliviaRodrigoVEVO")
    draft = ("Olivia Rodrigo - stupid song (Official Music Video)", "Olivia Rodrigo")
    assert _same_clean(stored, draft), (
        compute_canonical_key_clean(*stored), compute_canonical_key_clean(*draft))
    # Both clean to "stupid song" / "Olivia Rodrigo" (prefix + bracket + VEVO).
    ct, ca = clean_title_artist(*stored)
    assert ct == "stupid song", ct
    assert ca == "OliviaRodrigo", ca


def test_case3_kpop_lyric_video_paren_name_matches():
    # 2026-06-15 miss: a YouTube K-pop lyric-video upload (artist name in parens,
    # title in single quotes, trailing "Lyric Video") must clean to the bare
    # title/artist and match the already-clean stored row. The lyric-video upload
    # signal is what gates the quoted-title extraction here. (The real title
    # carries the Hangul group "(BTS)"; ASCII placeholder used here per the
    # ASCII-only-in-code rule -- the cleaner strips any paren group regardless.)
    stored = ("Come Over", "BTS")
    draft = ("BTS (Bangtan Sonyeondan) 'Come Over' Lyric Video", "BTS")
    assert clean_title_artist(*draft) == ("Come Over", "BTS"), clean_title_artist(*draft)
    assert _same_clean(stored, draft), (
        compute_canonical_key_clean(*stored), compute_canonical_key_clean(*draft))


def test_lyric_video_apostrophe_title_not_mangled():
    # Widening the gate to lyric-video must NOT let a mid-word apostrophe get
    # mis-parsed as a quoted K-pop title: the opening quote needs whitespace
    # before it, which no apostrophe in "Don't"/"Believin'" has.
    ct, _ = clean_title_artist("Don't Stop Believin' (Lyric Video)", "Journey")
    assert ct == "Don't Stop Believin'", ct


def test_soundtrack_suffix_resolves_to_base_song():
    # 2026-06-27 miss: a Spotify single carrying a '- From "Movie"' soundtrack
    # tail must clean to the base title and resolve to the already-calibrated
    # plain row (which would otherwise re-list as awaiting-lyrics every day).
    stored = ("I Knew It, I Knew You", "Taylor Swift")
    draft = ('I Knew It, I Knew You - From "Toy Story 5"', "Taylor Swift")
    assert clean_title_artist(*draft) == stored, clean_title_artist(*draft)
    assert _same_clean(stored, draft), (
        compute_canonical_key_clean(*stored), compute_canonical_key_clean(*draft))
    db = _FakeDB([_stored_row(3244, *stored)])
    res = resolve_song_identity(db, *draft)
    assert res.song_id == 3244 and res.via == "clean", (res.song_id, res.via)


def test_ost_track_number_and_soundtrack_bracket_cleaned():
    # A recurring OST upload: leading track-number prefix + a "(... Soundtrack)"
    # provenance bracket must both strip so the title matches the plain row.
    ct, _ = clean_title_artist(
        "34. Flower Man (DELTARUNE Chapter 5 Soundtrack)",
        "Toby Fox & @Cametek.CamelliaOfficial - Toby Fox")
    assert ct == "Flower Man", ct


def test_ost_reentry_resolves_to_multiprimary_stored_row():
    # End to end: the crufty OST re-entry (track-number + soundtrack bracket in
    # the title, garbled channel handle for the 2nd artist) must resolve to the
    # already-calibrated two-primary stored row via the lead-key vs exact-key
    # bridge -- otherwise it re-lists as awaiting-lyrics every single day.
    stored = ("Flower Man", "Toby Fox & Camellia")
    draft = ("34. Flower Man (DELTARUNE Chapter 5 Soundtrack)",
             "Toby Fox & @Cametek.CamelliaOfficial - Toby Fox")
    db = _FakeDB([_stored_row(3710, *stored)])
    res = resolve_song_identity(db, *draft)
    assert res.song_id == 3710 and res.via == "clean", (res.song_id, res.via)


def test_track_number_only_strips_dot_form():
    # A real title that merely starts with digits (no "N. " dot-space shape) is
    # untouched -- only the dot-anchored track-number prefix is a prefix.
    ct, _ = clean_title_artist("99 Luftballons", "Nena")
    assert ct == "99 Luftballons", ct
    ct2, _ = clean_title_artist("24K Magic", "Bruno Mars")
    assert ct2 == "24K Magic", ct2


def test_soundtrack_bracket_only_strips_at_end():
    # The soundtrack drop is end-anchored inside the bracket: a bracket whose
    # inner ends in "soundtrack" drops, a real title word does not.
    ct, _ = clean_title_artist("Main Theme (Original Motion Picture Soundtrack)", "Composer")
    assert ct == "Main Theme", ct
    # A version-meaningful bracket that does not end in "soundtrack" is preserved.
    ct2, _ = clean_title_artist("Main Theme (Live Version)", "Composer")
    assert ct2 == "Main Theme (Live Version)", ct2


def test_distinct_lead_not_merged_by_exact_lead_key():
    # The new `canonical_key = :lk` clause must not merge a different lead: a
    # stored solo row and a same-title draft with a DIFFERENT lead stay distinct.
    stored = ("Radio", "Some Artist")
    draft = ("Radio", "Totally Different Lead")
    db = _FakeDB([_stored_row(50, *stored)])
    res = resolve_song_identity(db, *draft)
    assert res.song_id is None and res.via == "new", (res.song_id, res.via)


def test_soundtrack_suffix_parenthetical_form():
    # The parenthetical '(From "Movie")' form cleans the same way.
    ct, _ = clean_title_artist('Speechless (From "Aladdin")', "Naomi Scott")
    assert ct == "Speechless", ct


def test_real_title_ending_in_from_not_eaten():
    # The strip requires a quoted work after "From" -- a real title that just
    # ends in the word "from" (no quotes) must survive untouched.
    ct, _ = clean_title_artist("Where Do We Go From Here", "Filter")
    assert ct == "Where Do We Go From Here", ct


def test_no_cruft_clean_key_equals_exact():
    # A plain song carries no cruft -> the clean key equals the exact key.
    pair = ("Anti-Hero", "Taylor Swift")
    assert compute_canonical_key_clean(*pair) == compute_canonical_key(*pair)


def test_real_title_audio_not_eaten():
    # A song literally titled "Audio" (no brackets) must survive -- the bracket
    # strip applies only INSIDE brackets.
    ct, _ = clean_title_artist("Audio", "LSD")
    assert ct == "Audio", ct


def test_remix_preserved_distinct():
    # Version-meaningful words are never stripped: a remix is a distinct work.
    base = ("Closer", "The Chainsmokers")
    remix = ("Closer (Remix)", "The Chainsmokers")
    assert compute_canonical_key_clean(*base) != compute_canonical_key_clean(*remix)


def test_apostrophe_title_not_mangled():
    # "Rock 'n' Roll" has no MV signal and a non-label artist -> the quote
    # extractor must NOT fire (it would otherwise leave "n").
    ct, _ = clean_title_artist("Rock 'n' Roll", "Led Zeppelin")
    assert ct == "Rock 'n' Roll", ct


def test_bracketed_credit_and_pipe_tail():
    ct, _ = clean_title_artist("Industry Baby (feat. Jack Harlow) | @LilNasX", "Lil Nas X")
    assert ct == "Industry Baby", ct


def test_lyric_video_and_topic_channel():
    stored = ("Espresso (Official Lyric Video)", "Sabrina Carpenter - Topic")
    draft = ("Espresso", "Sabrina Carpenter")
    assert _same_clean(stored, draft)


def test_different_artist_same_title_stays_distinct():
    # Songs-not-artists: same title by a different primary artist must NOT merge.
    a = ("Hold On", "Justin Bieber")
    b = ("Hold On", "Wilson Phillips")
    assert compute_canonical_key_clean(*a) != compute_canonical_key_clean(*b)


# --- feeder-display guard (is_feeder_upload / clean_feeder_display) ---------- #
# These protect the STORED display title/artist at the write chokepoint: only a
# raw platform-upload string is rewritten; a normal chart title is left verbatim.

def test_feeder_upload_detected_for_cruft():
    for t, a in [
        ("Olivia Rodrigo - stupid song (Official Music Video)", "OliviaRodrigoVEVO"),
        ("Olivia Rodrigo - honeybee (Lyric Video)", "Olivia Rodrigo"),
        ("G Herbo - Mad People (Official Video)", "G Herbo"),
        ("Espresso", "Sabrina Carpenter - Topic"),
        ("ILLIT 'ICONIC BY MISTAKE' Official MV", "HYBE LABELS"),
    ]:
        assert is_feeder_upload(t, a), (t, a)


def test_feeder_upload_not_flagged_for_clean_titles():
    # Legit chart titles -- a feature paren, a remix tag, an apostrophe, a bare
    # title -- must NOT be treated as upload cruft (so display is left intact).
    for t, a in [
        ("GIRLS (feat. Kehlani) - Remix", "The Kid LAROI, Kehlani"),
        ("good 4 u", "Olivia Rodrigo"),
        ("Somethin' Stupid", "Frank & Nancy Sinatra"),
        ("Anti-Hero", "Taylor Swift"),
    ]:
        assert not is_feeder_upload(t, a), (t, a)


def test_clean_feeder_display_leaves_feature_titles_verbatim():
    # The display cleaner must be a no-op on a non-upload title: the feature paren
    # stays (clean_title_artist WOULD strip it -- that is only for the dedup key).
    pair = ("GIRLS (feat. Kehlani) - Remix", "The Kid LAROI, Kehlani")
    assert clean_feeder_display(*pair) == pair


def test_clean_feeder_display_recovers_vevo_artist_spacing():
    # The VEVO channel cleans to a despaced "OliviaRodrigo"; the title prefix
    # carries the real spacing, so the display artist must be "Olivia Rodrigo"
    # (matching the existing artist entity, not minting "OliviaRodrigo").
    ct, ca = clean_feeder_display(
        "Olivia Rodrigo - stupid song (Official Music Video)", "OliviaRodrigoVEVO")
    assert (ct, ca) == ("stupid song", "Olivia Rodrigo"), (ct, ca)


def test_clean_feeder_display_same_key_as_raw():
    # Whatever the display rewrite, the normalized canonical key is unchanged --
    # cleaning never moves a feeder row onto a different identity.
    raw = ("Olivia Rodrigo - stupid song (Official Music Video)", "OliviaRodrigoVEVO")
    ct, ca = clean_feeder_display(*raw)
    assert compute_canonical_key(ct, ca) == compute_canonical_key_clean(*raw)


def test_trailing_channel_credit_in_title_stripped():
    # The REAL 2026-07-07 miss: the feeder put the 2nd-artist channel credit in
    # the TITLE ("... - Toby Fox & @Cametek.CamelliaOfficial") with a bare "Toby
    # Fox" artist. The trailing "- <...@handle>" credit must strip so the title
    # cleans to the bare song.
    ct, _ = clean_title_artist(
        "34. Flower Man (DELTARUNE Chapter 5 Soundtrack) - Toby Fox & @Cametek.CamelliaOfficial",
        "Toby Fox")
    assert ct == "Flower Man", ct


def test_ost_channel_credit_in_title_resolves_via_lead_key():
    # End to end for the real split: the crufty re-entry resolves to the stored
    # two-primary row via the lead-primary clean key (Toby Fox), instead of
    # re-listing as awaiting-lyrics every day.
    stored = ("Flower Man", "Toby Fox & Camellia")
    draft = ("34. Flower Man (DELTARUNE Chapter 5 Soundtrack) - Toby Fox & @Cametek.CamelliaOfficial",
             "Toby Fox")
    db = _FakeDB([_stored_row(3710, *stored)])
    res = resolve_song_identity(db, *draft)
    assert res.song_id == 3710, (res.song_id, res.via)


def test_bare_trailing_channel_handle_stripped():
    ct, _ = clean_title_artist("Some Song @SomeChannel", "Artist")
    assert ct == "Some Song", ct


def test_trailing_dash_without_handle_preserved():
    # A real ' - subtitle' with no @handle must survive (the strip is gated on @).
    ct, _ = clean_title_artist("Symphony No. 5 - Movement II", "Beethoven")
    assert ct == "Symphony No. 5 - Movement II", ct


def test_short_film_bracket_cleaned_and_resolves():
    # The 2026-07-07 Piece Of Your Love miss: the stored row's clean key was
    # polluted with "shortfilm" because a "(Short Film)" bracket was not stripped.
    # It is now cruft, so a "(Short Film)" upload cleans to the bare title and a
    # freshly-stored row carries a clean key that matches the plain song.
    ct, _ = clean_title_artist("Piece Of Your Love (Short Film)", "Rod Wave")
    assert ct == "Piece Of Your Love", ct
    assert _same_clean(("Piece Of Your Love", "Rod Wave"),
                       ("Piece Of Your Love (Short Film)", "Rod Wave"))


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
