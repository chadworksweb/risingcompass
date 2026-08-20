"""deadpan_line placard-form guard.

The deadpan_line spec has lived only as prompt text in `ether_tagger._VOICE_BLOCK`
since the Ether Art Chart shipped: a FLAT museum-placard FRAGMENT naming what the
song is, roughly len(title) + len(artist) characters, no terminal period, no
leading article. Register examples: "Possessive-jealousy spiral",
"Substance-soaked party hymn", "Head-to-toe body ogling".

Nothing enforced it on the write path. The server tagger lane carried only a flat
MAX_DEADPAN_LENGTH truncation (3499e8d, 2026-04-28, which replaced a much tighter
2x(title+artist) cap), and the TERMINAL lane -- Claude-Code-supplied backfill and
lyrics-supply writes -- passes `deadpan_line` straight through `_store_calibration`
-> `song_sync` to the column with no cap and no form check at all. Operator drift
therefore landed silently: placard fragments (avg ~32 chars) degraded into full
sentences with terminal periods across 2026-06-27 -> 2026-07-25 (the 2003 year-end
#11-20 batch wrote 10/10 as sentences at avg 78 chars).

This module is the missing enforcement, mirroring `summary_guard.py`:
  - terminal / operator write path (`_store_calibration`, allow_prose_generation
    False): HARD-FAIL, so drift cannot land. The operator rewrites to placard form.
  - server tagger lane (`ether_tagger._sanitize`): supplies the spec-derived length
    cap that replaces the flat ceiling.

Never silent-strip a form violation: a truncated sentence is worse than a rejected
one. Length alone is still truncated in the tagger lane (that path has no operator
to re-run it).
"""

import re

# Spec: "~ len(artist) + len(title)". FLOOR keeps a short title+artist pair from
# producing an unusably tight cap (the 2026-04-28 bug: "Jane!" by "The Long Faces"
# got a 38-char cap and chopped a legitimate placard mid-thought). CEILING keeps a
# long featured-artist credit from licensing a sentence.
DEADPAN_LENGTH_FLOOR = 45
DEADPAN_LENGTH_CEILING = 60

# Placards name; they do not open like a narrated sentence.
_SENTENCE_OPENER_RE = re.compile(
    r"(?i)^\s*(a|an|the|two|three|four|she|he|they|it|someone|everyone|"
    r"nobody|this|these|his|her|their)(?=\s)"
)

# Any terminal sentence punctuation, or an internal sentence break.
_TERMINAL_PUNCT_RE = re.compile(r"[.!?]\s*$")
_INTERNAL_BREAK_RE = re.compile(r"[.!?]\s+\S")


def deadpan_length_cap(title: str = "", artist: str = "") -> int:
    """The spec cap for one song: ~len(title)+len(artist), clamped."""
    natural = len(title or "") + len(artist or "")
    return max(DEADPAN_LENGTH_FLOOR, min(DEADPAN_LENGTH_CEILING, natural))


def deadpan_violations(deadpan, title: str = "", artist: str = "") -> list:
    """Return a list of spec violations. Empty list == clean."""
    problems: list = []
    if deadpan is None:
        return problems
    if not isinstance(deadpan, str):
        return ["deadpan_line is not a string"]

    text = deadpan.strip()
    if not text:
        return problems

    if _TERMINAL_PUNCT_RE.search(text):
        problems.append(
            "ends with sentence punctuation (the placard is a fragment, no period)"
        )
    if _INTERNAL_BREAK_RE.search(text):
        problems.append("contains more than one sentence (one fragment per song)")
    if _SENTENCE_OPENER_RE.match(text):
        problems.append(
            "opens like a narrated sentence (drop the article/pronoun subject and "
            "name the thing: \"Possessive-jealousy spiral\")"
        )

    cap = deadpan_length_cap(title, artist)
    if len(text) > cap:
        problems.append(f"is {len(text)} chars, over the {cap}-char placard cap")

    return problems


DEADPAN_RULES_NUDGE = (
    "deadpan_line is a FLAT museum-placard FRAGMENT naming what the song IS: "
    "roughly len(title)+len(artist) characters, no terminal period, no leading "
    "article, one per song. Descriptive adjectives are allowed, evaluative ones "
    "are not, and it names the lyrics rather than the artist."
    " Name what is present. A placard naming a thing by what it lacks is a hole "
    "where the description should be, and at four or five words a negation spends "
    "one of them saying nothing. The test: take the negation out, and if the "
    "fragment still names the song it was doing other work and it stays; if it "
    "collapses, the naming has not been found yet, and it exists and is usually "
    "shorter. A crush kept quiet. A wanting that is one-sided. A worth its owner "
    "doubts."
)

CORRECTIVE_NUDGE = (
    "Rewrite as a naming fragment, e.g. \"Revenge breakup song\", "
    "\"Substance-soaked party hymn\", \"Head-to-toe body ogling\", "
    "\"Money-as-armor manifesto\"."
)
