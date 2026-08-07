"""Unit tests for the spelled-out "and" credit separator (no DB).

parse_artist_string splits credits on "&" but not on the word "and", so
"Kool & the Gang" keyed on the primary ("kool") while "Kool and the Gang" kept
the whole string -- the same act minted a SECOND songs row. Wikipedia year-end
charts spell it out and the feeders use the symbol, so the corpus grew four
duplicate pairs (Endless Love, Do That to Me One More Time, Celebration, Kiss on
My List) plus a feeder stub shadowing Die with a Smile, each carrying stray
chart appearances on a defaulted current year, before it was caught 2026-08-07.

song_identity._collapse_and_connector normalizes the word form to the symbol
before parsing, so both spellings resolve to one identity.

Run standalone:  python tests/test_song_identity_and_connector.py
Or via pytest:   python -m pytest tests/test_song_identity_and_connector.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.song_identity import (
    _collapse_and_connector,
    clean_artist_set_key,
    compute_canonical_key,
    compute_canonical_key_clean,
    extract_primary_artist,
)

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


def check_same(label, a, b):
    if a != b:
        FAILURES.append(f"{label}: {a!r} != {b!r}")


# The four real duplicate pairs, plus the collaboration form.
PAIRS = [
    ("Celebration", "Kool & the Gang", "Kool and the Gang"),
    ("Do That to Me One More Time", "Captain & Tennille", "Captain and Tennille"),
    ("Endless Love", "Diana Ross & Lionel Richie", "Diana Ross and Lionel Richie"),
    ("Die with a Smile", "Lady Gaga & Bruno Mars", "Lady Gaga and Bruno Mars"),
    ("Enemy", "Imagine Dragons & JID", "Imagine Dragons and JID"),
]

for title, amp, word in PAIRS:
    check_same(
        f"canonical_key({title})",
        compute_canonical_key(title, amp),
        compute_canonical_key(title, word),
    )
    check_same(
        f"clean_key({title})",
        compute_canonical_key_clean(title, amp),
        compute_canonical_key_clean(title, word),
    )
    check_same(
        f"clean_artist_set_key({title})",
        clean_artist_set_key(amp),
        clean_artist_set_key(word),
    )

# The comma form already keyed on the primary; the word form must join it.
check_same(
    "comma form matches word form",
    compute_canonical_key("Die with a Smile", "Lady Gaga, Bruno Mars"),
    compute_canonical_key("Die with a Smile", "Lady Gaga and Bruno Mars"),
)

# A featured credit still collapses to the primary, both spellings.
check_same(
    "featuring collapses to primary",
    compute_canonical_key("Wait for U", "Future ft. Drake and Tems"),
    compute_canonical_key("Wait for U", "Future"),
)

# Word-boundary safety: "and" inside a name is never a separator.
check("no split inside a word", _collapse_and_connector("Sandy Posey"), "Sandy Posey")
check("no split on Andy", _collapse_and_connector("Andy Williams"), "Andy Williams")
check("empty passes through", _collapse_and_connector(""), "")
check("none passes through", _collapse_and_connector(None), None)
check(
    "primary of a word-separated credit",
    extract_primary_artist("Diana Ross and Lionel Richie"),
    "Diana Ross",
)
# A title carrying "and" is untouched -- only the ARTIST is collapsed.
check_same(
    "title keeps its own 'and'",
    compute_canonical_key("Rock and a Hard Place", "Bailey Zimmerman"),
    compute_canonical_key("Rock and a Hard Place", "Bailey Zimmerman"),
)
if "rockandahardplace" not in compute_canonical_key("Rock and a Hard Place", "X"):
    FAILURES.append("title 'and' was collapsed; it must not be")

if FAILURES:
    print("FAIL (%d)" % len(FAILURES))
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("ok: %d checks passed" % (len(PAIRS) * 3 + 8))
