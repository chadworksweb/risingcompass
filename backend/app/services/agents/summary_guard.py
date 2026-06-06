"""charge_summary absence/verdict-framing guard.

The charge_summary must be PURE POSITIVE DESCRIPTION of what a song IS and does
-- never what it lacks, fails, avoids, "reaches for", or a verdict on whether it
works (those belong in the reasoning, not the public one-line summary). The rule
has lived only as prompt text in `SUMMARY_VOICE_RULES` since c940c57; this module
is the missing enforcement, mirroring the CONTAMINATION CHECK guard in
`calibrator.py`.

Used in two places:
  - `calibrator.py` retry loop: if the model's charge_summary trips the detector,
    retry once with a corrective nudge, then proceed with a loud log (parallels
    `_CONTAM_LINE_RE`).
  - `song_sync.store_calibrated_song`: a write-time loud-log so terminal-supplied
    calibrations (backfill, album work) and any other direct writer surface drift
    too.

DESIGN: conservative + high-precision. Negations are anchored to song-QUALITY
verbs/nouns (explore, develop, move, substance, depth...) so that legitimate
CONTENT negation describing what a song is about ("a narrator who isn't ready to
commit") does NOT trip. Ambiguous descriptive constructions that the
gold-standard corpus uses legitimately ("rather than", a bare "never", "but
never") are intentionally NOT matched. Flag-and-retry / flag-and-log, never
silent-strip -- a false positive must never mangle a valid summary, only surface
it.
"""

import re

# Song-quality verbs/nouns. Absence framing aims a negation at one of these
# ("doesn't explore", "without real substance", "never moves through it").
_QUALITY = (
    r"explor|examin|develop|process|resolv|question|dig|"
    r"interrogat|earn|deliver|substance|depth|growth|reflect|transform|"
    r"unpack|grapple|introspect|"
    r"mov(?:e|es|ing)\s+(?:through|past|beyond|forward)|"
    r"go(?:es|ing)?\s+(?:deep|deeper|beyond|further)"
)

_SUMMARY_ABSENCE_RE = re.compile(
    r"(?i)(?:"
    # negation aimed at a quality word within a few tokens
    r"(?:does\s+not|doesn[’']?t|do\s+not|don[’']?t|didn[’']?t|never|"
    r"fails?\s+to|without(?:\s+ever|\s+any|\s+real)?|won[’']?t|can[’']?t)"
    r"\W+(?:\w+\W+){0,3}?(?:" + _QUALITY + r")"
    # standalone high-signal tells. NOTE: a bare "reach(es|ing) for" was removed
    # after it false-positived on legitimate positive description ("reaching for
    # unconditional love" = what the song DOES, not absence-framing). The prompt
    # SUMMARY CHECK still warns against the "reaches for X but ..." shape; the
    # quality-anchored negations above carry the real enforcement.
    r"|no\s+real\s+(?:\w+\W+){0,2}?(?:" + _QUALITY + r")"
    r"|\blacks?\b"
    r"|(?:falls?|stops?)\s+short\b"
    r"|nothing\s+(?:underneath|beneath)\b"
    r")"
)

# Pull the charge_summary value straight out of the model's JSON text so the
# detector only inspects that field (not contamination_note / dogma_note, which
# may legitimately paraphrase negative content). Handles escaped quotes.
_SUMMARY_FIELD_RE = re.compile(r'"charge_summary"\s*:\s*"((?:[^"\\]|\\.)*)"')

CORRECTIVE_NUDGE = (
    "The charge_summary used absence or verdict framing -- it described what the "
    "song does NOT do, what it lacks or fails or 'reaches for', or judged whether "
    "it works. Rewrite the charge_summary as PURE POSITIVE DESCRIPTION of what the "
    "song IS and what its lyrics actually do (subject, stance, relational frame, "
    "imagery). State only what is present. No 'doesn't', 'without', 'fails to', "
    "'reaches for', 'no real', 'lacks', no verdict clauses. Re-emit the full "
    "structured format, then the JSON."
)


def summary_has_absence_framing(summary: str | None) -> bool:
    """True if the summary contains high-signal absence/verdict framing."""
    if not summary:
        return False
    return bool(_SUMMARY_ABSENCE_RE.search(summary))


def summary_from_json_text(json_text: str | None) -> str | None:
    """Best-effort extraction of the charge_summary value from raw model JSON
    text, without a full parse. Returns None if not found."""
    if not json_text:
        return None
    m = _SUMMARY_FIELD_RE.search(json_text)
    return m.group(1) if m else None
