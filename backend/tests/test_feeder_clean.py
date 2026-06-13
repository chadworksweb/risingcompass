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

from app.services.feeder_clean import clean_title_artist
from app.services.song_identity import compute_canonical_key, compute_canonical_key_clean


def _same_clean(a, b):
    """The two (title, artist) pairs resolve to the SAME clean key."""
    return compute_canonical_key_clean(*a) == compute_canonical_key_clean(*b)


def test_case1_kpop_label_channel_matches():
    # Stored row id 3297 vs today's draft string.
    stored = ("ILLIT 'ICONIC BY MISTAKE' Official MV", "HYBE LABELS")
    draft = ("ICONIC BY MISTAKE", "ILLIT, LE SSERAFIM")
    assert clean_title_artist(*stored) == ("ICONIC BY MISTAKE", "ILLIT")
    assert _same_clean(stored, draft), (
        compute_canonical_key_clean(*stored), compute_canonical_key_clean(*draft))


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
