"""Supplied-prose form guard for the terminal / operator write path.

The listener + societal effects prose have a tight, documented shape (the VOICE
constants in `listener_effects_prose.py` / `societal_effects_prose.py`): exactly
two paragraphs, under 110 words (listener) or 130 (societal), third person with
no second-person address, no em-dashes, and none of the surface tells inventoried
in `prose_tell_guard`.

The song title is masked out before the second-person scan (the voice requires
naming it once, and titles like "Whatever You Like" carry a pronoun); callers
pass `title=` for that. A second-person token anywhere else still hard-fails.

Enforcement existed only where the SERVER generates prose (the two generator
modules call `prose_tell_guard.hard_findings` on their own output) and in
`scripts/regenerate_prose.py`. The terminal lane -- Claude-Code-supplied backfill
and lyrics-supply writes -- passed both strings straight through
`_store_calibration` -> `song_sync` to the columns with no check at all, the same
hole that let `deadpan_line` drift from placards into sentences (see
`deadpan_guard.py`). Measured on the year-end #11-20 pass before this guard: word
counts swinging 75 to 205 against a 110 cap, 133 rows carrying three or more
paragraphs, and 554 of 906 prose blocks tripping a HARD tell.

Posture matches the summary and deadpan guards: the operator IS the model, so the
terminal write HARD-FAILS and the operator rewrites. Never silent-strip.

The tell scan is lane-aware and deficit-aware, which matters: the collective-noun
tell is a listener-lane bleed (a society IS the subject of societal prose), and
rule-O deficit language is legitimate on a genuinely negative song. Pass the lane
and the song's tier, never the defaults.
"""

from app.services.prose_tell_guard import hard_findings

LISTENER_WORD_CAP = 110
SOCIETAL_WORD_CAP = 130
PARAGRAPH_COUNT = 2

# Second-person address. The listener lane reports on "an individual" in the third
# person; the societal lane addresses no reader either.
_SECOND_PERSON = ("you", "your", "yours", "you're", "youre", "yourself")

# Em-dash and en-dash: banned outright in both voices.
_DASHES = ("—", "–")

# Tiers where corrosion / deficit language is the honest report, so rule-O is off.
_DEFICIT_COLORS = frozenset({"orange", "red"})


def _words(text: str) -> int:
    return len(text.split())


def _paragraphs(text: str) -> list:
    return [p for p in (b.strip() for b in text.split("\n\n")) if p]


def prose_violations(prose, lane: str, rubric_color: str = "",
                     title: str = "") -> list:
    """Return a list of spec violations for one prose block. Empty == clean.

    `lane` is "listener" or "societal"; `rubric_color` selects deficit tolerance.
    """
    problems: list = []
    if prose is None:
        return problems
    if not isinstance(prose, str):
        return [f"{lane} prose is not a string"]

    text = prose.strip()
    if not text:
        return problems

    paras = _paragraphs(text)
    if len(paras) != PARAGRAPH_COUNT:
        problems.append(
            f"has {len(paras)} paragraphs (the voice is exactly {PARAGRAPH_COUNT})"
        )

    cap = LISTENER_WORD_CAP if lane == "listener" else SOCIETAL_WORD_CAP
    count = _words(text)
    if count > cap:
        problems.append(f"is {count} words, over the {cap}-word cap")

    # The voice REQUIRES naming the exact song title once, and plenty of titles
    # carry a second-person pronoun ("Whatever You Like", "With You"). Mask the
    # title before the second-person scan so the required mention cannot trip
    # the check; a "you" anywhere outside the title still fails. Same family as
    # the quote-guard title gotcha logged on the 2005 #13 write.
    lowered = text.lower()
    if title:
        lowered = lowered.replace(title.strip().lower(), " ")
    hits = [w for w in _SECOND_PERSON if _token_present(lowered, w)]
    if hits:
        problems.append(
            "addresses the reader in the second person (" + ", ".join(sorted(hits))
            + "); the subject is the individual or the population, in third person"
        )

    for dash in _DASHES:
        if dash in text:
            problems.append("contains an em-dash or en-dash (use commas, periods, "
                            "or parentheses)")
            break

    allow_deficit = (rubric_color or "").lower() in _DEFICIT_COLORS
    tells = hard_findings(text, lane, allow_deficit)
    if tells:
        problems.append(
            "trips hard prose tells: "
            + "; ".join(f"{t.name} [{t.snippet.strip()}]" for t in tells)
        )

    return problems


def _token_present(lowered_text: str, word: str) -> bool:
    """Whole-word membership without importing re for one check."""
    padded = " " + "".join(
        c if (c.isalnum() or c == "'") else " " for c in lowered_text
    ) + " "
    return f" {word} " in padded


PROSE_RULES_NUDGE = (
    "Effects prose is exactly two paragraphs in the senior-academic clinical "
    "register, under 110 words (listener) or 130 (societal), third person with the "
    "individual or the population as the subject that changes, no second-person "
    "address, no em-dashes, and none of the stock tells (pseudo-cleft closers, "
    "stock paragraph pivots, 'reads as', subject-lock on the words or the narrator)."
)

CORRECTIVE_NUDGE = (
    "Rewrite the offending block to the voice constants in "
    "listener_effects_prose.py / societal_effects_prose.py and re-run."
)
