"""Verbatim-lyric guard: the strip, the title carve-out, and the reporting.

The carve-out exists because both prose voices REQUIRE naming the exact title
once, and for most pop songs the title IS a lyric line. Before it, naming the
title read as quoting the song and the whole sentence went (song 2797,
2026-08-12: both lanes lost their thesis sentence, silently).

Run standalone:  python tests/test_lyric_quote_guard.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.lyric_quote_guard import (  # noqa: E402
    has_verbatim_overlap,
    scrub_calibration_quotes,
    strip_verbatim_quotes,
    strip_verbatim_quotes_detailed,
)

# The real shape of the collision: the title is a six-word run in the chorus.
TITLE = "So Easy (To Fall In Love)"
LYRICS = (
    "I could be the twist, the one to make you stop\n"
    "'Cause I make it so easy to fall in love\n"
    "So, come give me a call, and we'll fall into us\n"
    "It's so easy to fall in love with me\n"
)


def test_plain_quote_is_stripped():
    prose = ("An individual grows confident. The narrator sings come give me a "
             "call and we'll fall into us. Calm follows.")
    cleaned, removed = strip_verbatim_quotes_detailed(prose, LYRICS)
    assert len(removed) == 1
    assert "come give me a call" in removed[0]
    assert "Calm follows." in cleaned


def test_title_mention_is_stripped_without_the_carve_out():
    prose = f"An individual who plays {TITLE} on repeat grows confident. Calm follows."
    cleaned, removed = strip_verbatim_quotes_detailed(prose, LYRICS)
    assert len(removed) == 1, "the title is a 6-word chorus run, so it reads as a quote"
    assert "grows confident" not in cleaned


def test_title_mention_survives_with_the_carve_out():
    prose = f"An individual who plays {TITLE} on repeat grows confident. Calm follows."
    cleaned, removed = strip_verbatim_quotes_detailed(prose, LYRICS, title=TITLE)
    assert removed == []
    assert cleaned == prose


def test_only_one_title_mention_is_excused():
    prose = (f"An individual who plays {TITLE} on repeat grows confident. "
             f"Repeated exposure to {TITLE} lowers the threshold.")
    cleaned, removed = strip_verbatim_quotes_detailed(prose, LYRICS, title=TITLE)
    assert len(removed) == 1, "the allowance is one per block"
    assert "grows confident" in cleaned
    assert "lowers the threshold" not in cleaned


def test_title_embedded_in_a_longer_quote_still_strips():
    # Reaches past the title span into the next lyric words: a real quote.
    prose = ("The chorus runs so easy to fall in love with me and repeats. "
             "Calm follows.")
    cleaned, removed = strip_verbatim_quotes_detailed(prose, LYRICS, title=TITLE)
    assert len(removed) == 1
    assert "Calm follows." in cleaned


def test_short_title_changes_nothing():
    # "I Love a Rainbow" is 4 words: under MIN_RUN, so it can never trigger a hit
    # and the carve-out has no effect either way.
    lyrics = "I love a rainbow\nFirst come clouds then comes rain\n"
    prose = "An individual who plays I Love a Rainbow on repeat settles."
    assert strip_verbatim_quotes_detailed(prose, lyrics)[1] == []
    assert strip_verbatim_quotes_detailed(prose, lyrics, title="I Love a Rainbow")[1] == []


def test_paragraph_structure_is_preserved():
    prose = (f"An individual who plays {TITLE} on repeat grows confident. Calm follows."
             "\n\n"
             "The narrator sings come give me a call and we'll fall into us. Ease arrives.")
    cleaned, removed = strip_verbatim_quotes_detailed(prose, LYRICS, title=TITLE)
    assert len(removed) == 1
    assert cleaned.count("\n\n") == 1
    assert cleaned.endswith("Ease arrives.")


def test_wrapper_keeps_its_shape():
    prose = f"An individual who plays {TITLE} on repeat grows confident."
    assert strip_verbatim_quotes(prose, LYRICS) == ("", True)
    assert strip_verbatim_quotes(prose, LYRICS, title=TITLE) == (prose, False)


def test_scrub_passes_the_title_to_prose_but_not_notes():
    calibration = {
        "listener_effects_prose": (
            f"An individual who plays {TITLE} on repeat grows confident. "
            "Calm follows, and the threshold for stimulation drops in the individual."
        ),
        "contamination_note": "The narrator sings come give me a call and we'll fall into us.",
    }
    altered = scrub_calibration_quotes(calibration, LYRICS, title=TITLE)
    assert "listener_effects_prose" not in altered, "the title mention is excused"
    assert "contamination_note" in altered
    assert calibration["contamination_note"] is None


def test_overlap_check_is_unchanged():
    assert has_verbatim_overlap("come give me a call and we'll fall into us", LYRICS)
    assert not has_verbatim_overlap("nothing in common here at all", LYRICS)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
