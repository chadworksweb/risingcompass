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

DESIGN: two bands.
  (1) QUALITY-ANCHORED negations -- a negation aimed at a song-quality verb/noun
      (explore, develop, move, substance, depth...). High precision: legitimate
      CONTENT negation about a non-quality noun ("a narrator who isn't ready to
      commit") does NOT trip.
  (2) STRUCTURAL absence/verdict tells that are absence-framing or tier-defense
      almost everywhere they appear in a one-line summary, regardless of the
      following noun: "rather than", "instead of", "without <gerund>" (without
      pretending / grappling / resolving), a mid-summary verdict pivot ("..., though
      ...", "even as", "..., yet ..."), and bare verdict words (unearned, pat, glib,
      trite, contrived, cliched, heavy-handed, on-the-nose, half-baked). These USED
      to be excluded as corpus-legitimate; they are now caught by deliberate policy
      (Chad, 2026-06-23): the summary is PURE POSITIVE description of what the song
      IS, and "X rather than Y" / "X, though Y" is exactly the second-sentence pivot
      to assessment that the rule forbids. The false-positive cost is bounded by
      posture: the live calibrator RETRIES once then proceeds-with-loud-log (a false
      positive costs one retry, never a blanked summary), and the terminal/operator
      WRITE path HARD-FAILS (raises) so drift cannot land -- the operator rephrases
      to positive description. Never silent-strip.
"""

import re

# Song-quality verbs/nouns. Absence framing aims a negation at one of these
# ("doesn't explore", "without real substance", "never moves through it").
_QUALITY = (
    r"explor|examin|develop|process|resolv|question|dig|"
    r"interrogat|earn|deliver|substance|depth|growth|reflect|transform|"
    r"unpack|grappl|introspect|"
    r"mov(?:e|es|ing)\s+(?:through|past|beyond|forward)|"
    r"go(?:es|ing)?\s+(?:deep|deeper|beyond|further)"
)

# Verdict/assessment words that, when a "rather than"/"instead of" contrast lands
# on them, mark tier-defense bleeding into the summary -- as opposed to a legit
# CONTENT contrast ("giving rather than taking", "show love rather than say it"),
# which is left alone. Used only to anchor the contrast tells in band (2).
_VERDICT = (
    r"genuine|earned|unearned|slogan|mantra|sincere|growth|witness|"
    r"transcend|verdict|clich(?:e|ed)|trite|substance|depth"
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
    # band (2): structural absence/verdict tells (policy 2026-06-23, narrowed). A
    # "rather than"/"instead of" contrast is flagged ONLY when it lands on a
    # quality/verdict word within a few tokens (tier-defense bleed); a content
    # contrast ("giving rather than taking") is left alone. Plus "without <gerund>",
    # a mid-summary verdict pivot, and bare verdict words.
    r"|\b(?:rather\s+than|instead\s+of)\b\W+(?:\w+\W+){0,3}?(?:" + _QUALITY + r"|" + _VERDICT + r")"
    r"|\bwithout\s+(?:ever\s+)?\w+ing\b"
    r"|,\s*(?:though|yet)\b"
    r"|\beven\s+as\b"
    r"|\b(?:unearned|pat|glib|trite|contrived|clich(?:e|ed)|heavy-handed|on-the-nose|half-baked)\b"
    r")"
)

# Pull the charge_summary value straight out of the model's JSON text so the
# detector only inspects that field (not contamination_note / dogma_note, which
# may legitimately paraphrase negative content). Handles escaped quotes.
_SUMMARY_FIELD_RE = re.compile(r'"charge_summary"\s*:\s*"((?:[^"\\]|\\.)*)"')

CORRECTIVE_NUDGE = (
    "The charge_summary used absence, contrast, or verdict framing -- it described "
    "what the song does NOT do, what it lacks or falls short of, defined it by "
    "contrast ('X rather than Y', 'instead of', 'without <doing> Y'), pivoted to an "
    "assessment ('..., though ...', 'even as ...', '..., yet ...'), or judged whether "
    "it works (pat, glib, unearned, trite, contrived). Rewrite the charge_summary as "
    "PURE POSITIVE DESCRIPTION of what the song IS and what its lyrics actually do "
    "(subject, stance, relational frame, imagery). State only what is present. No "
    "'doesn't', 'without', 'rather than', 'instead of', 'fails to', 'no real', "
    "'lacks', no 'though/even as/yet' verdict pivots, no judgment words. If tempted "
    "to add a contrast or absence clause, replace it with another concrete detail of "
    "what the song does on the page. Re-emit the full structured format, then the JSON."
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


# --- genre / tier-color / title guards (Chad 2026-06-28) ------------------- #
# Three more hard rules for any reader-facing summary or editorial:
#   - no song TITLES. The summary describes what the song IS; it does not name
#     the song. The editorial describes the reading's charge, not its track list.
#   - no GENRE words. Genres describe the SOUND/medium; the compass reads the
#     WORDS on the page, and "words don't have genres" (Chad). A genre term in a
#     summary is reading the medium, not the lyrics.
#   - no tier COLOR names. Refer to a tier by its LABEL, never its color:
#     Corrupted (not "red"), Degraded (not "orange"), Decent (not "green"),
#     Elevated (not "blue"), Ascended (not "violet").
# Same posture as the absence guard: SURFACE the drift (raise on the operator
# write paths, the terminal per-song lane and the editorial endpoint), never
# silent-strip. Bounded false positives (a genre word used as content, a color
# used as imagery, a one-word title that is also a topic) cost a rephrase.

# Closed genre list, word-boundaried, case-insensitive. The most ambiguous
# everyday words (soul, house, blues, dance) are deliberately OMITTED: their
# content meaning is far more common than their genre meaning, so flagging them
# would cost more than it catches. "country" stays in (Chad's example), accepting
# that a literal-nation use occasionally trips and gets rephrased.
_GENRE_TERMS = (
    "pop", "rock", "country", "rap", "hip hop", "hip-hop", "hiphop", "trap",
    "reggaeton", "r&b", "rnb", "jazz", "folk", "metal", "punk", "edm",
    "techno", "dubstep", "funk", "gospel", "reggae", "ska", "disco", "indie",
    "grunge", "emo", "drill", "afrobeat", "afrobeats", "k-pop", "kpop",
    "bluegrass", "americana", "opera", "classical", "electronic", "dancehall",
    "salsa", "cumbia", "bachata", "merengue", "corrido", "corridos",
    "norteno", "banda", "mariachi", "flamenco", "synthpop", "electropop",
)
_GENRE_RE = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(g) for g in _GENRE_TERMS) + r")\b"
)

# Tier colors. Use the tier LABEL instead, never the color.
_TIER_COLOR_RE = re.compile(r"(?i)\b(?:violet|blue|green|orange|red)\b")

_NORM_RE = re.compile(r"[^a-z0-9 ]+")
_NORM_WS_RE = re.compile(r"\s+")


def _normalize_for_match(s: str | None) -> str:
    return _NORM_WS_RE.sub(" ", _NORM_RE.sub(" ", (s or "").lower())).strip()


def summary_has_genre_term(text: str | None) -> bool:
    """True if a musical-genre word appears (genres describe sound, not lyrics)."""
    return bool(text and _GENRE_RE.search(text))


def summary_has_tier_color(text: str | None) -> bool:
    """True if a tier COLOR name appears (use the tier label, not the color)."""
    return bool(text and _TIER_COLOR_RE.search(text))


def text_names_titles(text, titles, *, multiword_only=False, min_chars=4):
    """Return the supplied song titles that appear (token-boundaried) in text.

    `multiword_only` (per-song summaries): only flag titles of 2+ words, so a
    one-word title that is also the genuine topic ("Ganja", "Revenge") used as
    content does not trip; a lazy multi-word title-restatement still does.
    `min_chars`: skip very short normalized titles to avoid trivial coincidence.
    """
    nt = _normalize_for_match(text)
    if not nt:
        return []
    padded = f" {nt} "
    hits = []
    for title in titles or ():
        ntitle = _normalize_for_match(title)
        if len(ntitle) < min_chars:
            continue
        if multiword_only and " " not in ntitle:
            continue
        if f" {ntitle} " in padded:
            hits.append(title)
    return hits


def summary_violations(text, *, titles=(), check_absence=True,
                       titles_multiword_only=True, title_min_chars=4):
    """All guard violations for a reader-facing summary/editorial, as a list of
    short reason strings (empty list == clean).

    `check_absence` is for the per-song charge_summary; the editorial has a
    different voice (it names the reading's charge + undercurrent) and skips it.
    `titles_multiword_only` True for per-song (the song's own title), False for
    the editorial (any track name in the reading)."""
    out = []
    if check_absence and summary_has_absence_framing(text):
        out.append("absence/verdict framing")
    if summary_has_genre_term(text):
        out.append("genre word (genres describe the sound, not the lyrics)")
    if summary_has_tier_color(text):
        out.append("tier color name (use the tier label, not the color)")
    named = text_names_titles(
        text, titles, multiword_only=titles_multiword_only, min_chars=title_min_chars)
    if named:
        out.append("names a song title: " + ", ".join(repr(t) for t in named))
    return out


# Operator-facing reminder appended to a hard-fail. The label list is the swap
# for the color ban.
SUMMARY_RULES_NUDGE = (
    "Reader-facing summaries and editorials must: name NO song titles (describe "
    "the song, do not title it), use NO musical-genre words (read the lyrics, not "
    "the sound), and use NO tier color names (refer to a tier by its LABEL -- "
    "Corrupted / Degraded / Decent / Elevated / Ascended -- never red / orange / "
    "green / blue / violet)."
)
